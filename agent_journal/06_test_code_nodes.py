#!/usr/bin/env python3
"""
STEP 6 - Execute the n8n Code nodes' JavaScript and verify the output.

WHY THIS EXISTS
  The Node 3 and Node 5 Code blocks are the heart of the pipeline: ~250 lines
  of JavaScript including the entire report HTML/CSS, executed by n8n's
  sandboxed Code node (which runs on Node.js, not in a browser and not in
  Python). A typo, a bad regex escape, or a missing await would only surface
  as a red box in the n8n UI after you have already imported the workflow,
  created credentials and uploaded a CSV. Testing the JS here finds those
  errors in seconds.

  We execute the real JavaScript, extracted verbatim from the generated
  workflow JSON (not retyped - that is the whole point), against a mock of
  n8n's `$input`, `$()`, `Buffer` and `this.helpers.prepareBinaryData`.

  We use `node` to run it, because n8n's Code node IS a Node.js sandbox.
  Python cannot tell us whether the JS works.

USAGE
    python3 06_test_code_nodes.py
    requires: node on PATH (node --version)

EXIT CODES
    0 = all assertions passed
    1 = at least one assertion failed
"""

import json
import os
import re
import subprocess
import sys
import tempfile

JOURNAL_DIR = os.path.dirname(os.path.abspath(__file__))
# `product/` and `upload/` were promoted to the repo root during the
# restructure; agent_journal keeps only the build record.
REPO_ROOT = os.path.dirname(JOURNAL_DIR)
WORKFLOW_JSON = os.path.join(REPO_ROOT, "workflow", "review-pulso.workflow.json")

# The 5-row test CSV from the pipeline doc, section 7.
DOC_TEST_CSV = [
    "Ang bilis ng delivery at ang sarap ng food!",
    "Medyo matagal ang paghihintay at malamig na ang ulam.",
    "Okay lang naman, hindi maganda hindi rin pangit.",
    "Grabe ang panget ng packaging, basag na basag.",
    "Sulit na sulit, babalik ulit ako dito pramis",
]

# Predictions we stub the model with. Hand-written (rather than random) so the
# expected numbers below are checkable by eye.
STUB_RESULTS = [
    ("Positive", {"Negative": 0.021, "Neutral": 0.025, "Positive": 0.954}),
    ("Negative", {"Negative": 0.812, "Neutral": 0.151, "Positive": 0.037}),
    ("Neutral",  {"Negative": 0.401, "Neutral": 0.455, "Positive": 0.144}),  # gap 0.054 -> LOW CONF
    ("Negative", {"Negative": 0.934, "Neutral": 0.048, "Positive": 0.018}),
    ("Positive", {"Negative": 0.011, "Neutral": 0.033, "Positive": 0.956}),
]


def get_code(node_name):
    """Extract a node's jsCode verbatim from the generated workflow JSON."""
    with open(WORKFLOW_JSON) as fh:
        wf = json.load(fh)
    for n in wf["nodes"]:
        if n["name"] == node_name:
            return n["parameters"]["jsCode"]
    sys.exit(f"FATAL: node {node_name!r} not found in {WORKFLOW_JSON}")


# ---------------------------------------------------------------------------
# The JS harness. Wraps the real node code with mocks of everything n8n
# provides, then runs assertions and prints JSON results we check in Python.
# ---------------------------------------------------------------------------
HARNESS_JS = r"""
'use strict';
const assert = (cond, msg) => { if (!cond) throw new Error('ASSERT: ' + msg); };

const NODE3_CODE = __NODE3__;
const NODE5_CODE = __NODE5__;
const NODE7_CODE = __NODE7__;

// ---- n8n Code node runtime mock ------------------------------------------
function makeRunOnce(code, { items, trigger, node4Response }) {
  // Mock the $() helper: a function returning a chainable object with .first()
  const named = { 'Node 1': trigger, 'Node 3': null, 'Node 5': null };
  function $(name) {
    return { first: () => named[name] ? { json: named[name] } : { json: {} },
             all: () => [named[name] ? { json: named[name] } : { json: {} }] };
  }
  const $input = { first: () => items[0], all: () => items, last: () => items[items.length-1] };

  // Async function so we can await this.helpers.prepareBinaryData like the
  // real Code node does (the real one is an async function in n8n).
  const fn = new Function('items', '$', '$input', 'Buffer',
    'return (async () => { ' + code + ' })();');
  return fn(items, $, $input, Buffer);
}

(async () => {
  const out = {};

  // =====================================================================
  // NODE 3 tests
  // =====================================================================
  {
    const items = [
      { json: { review: DOC_REVIEWS[0] } },
      { json: { review: DOC_REVIEWS[1] } },
      { json: { review: DOC_REVIEWS[2] } },
      { json: { review: DOC_REVIEWS[3] } },
      { json: { review: DOC_REVIEWS[4] } },
    ];
    const trigger = { business_name: 'SariSari Store Malate', context: 'Q3 2026' };
    const r = await makeRunOnce(NODE3_CODE, { items, trigger });
    assert(Array.isArray(r) && r.length === 1, 'Node 3 must return exactly 1 item');
    const j = r[0].json;
    out.node3_itemCount = r.length;
    out.node3_reviewCount = j.reviews.length;
    out.node3_business = j.business_name;
    out.node3_context = j.context;
  }

  // -- dedupe: duplicate rows collapse
  {
    const items = [
      { json: { review: 'Sulit na sulit, babalik ulit ako dito pramis' } },
      { json: { review: 'Sulit na sulit, babalik ulit ako   dito pramis' } }, // ws differs
      { json: { review: '  SULIT NA SULIT, BABALIK ULIT AKO DITO PRAMIS  ' } }, // case differs
    ];
    const r = await makeRunOnce(NODE3_CODE, { items, trigger: { business_name: 'X' } });
    out.dedupe_count = r[0].json.reviews.length;
    // original casing/spacing must be preserved for the PDF
    out.dedupe_keptText = r[0].json.reviews[0];
  }

  // -- short/empty rows are dropped
  {
    const items = [
      { json: { review: 'A genuinely valid review' } },
      { json: { review: '' } },
      { json: { review: '   ' } },
      { json: { review: 'ab' } },           // < 3 chars
    ];
    const r = await makeRunOnce(NODE3_CODE, { items, trigger: { business_name: 'X' } });
    out.shortrow_count = r[0].json.reviews.length;
  }

  // -- alternate column names are honoured
  {
    const results = {};
    for (const col of ['text', 'comment', 'content', 'feedback', 'Review']) {
      const items = [{ json: { [col]: 'A real review under column ' + col } }];
      const r = await makeRunOnce(NODE3_CODE, { items, trigger: { business_name: 'X' } });
      results[col] = r[0].json.reviews.length;
    }
    out.altColumns = results;
  }

  // -- empty input must RETURN a structured rejection, not throw.
  // Changed 2026-10-01. This used to assert that Node 3 threw, which is exactly
  // the bug: a throw aborts the execution before Respond to Webhook runs, so
  // the client got n8n's generic {"message":"Error in workflow"} instead of the
  // message Node 3 had just constructed. Node 3b now routes this to a 400.
  {
    const items = [{ json: { review: '' } }, { json: { review: '  ' } }];
    let err = null;
    try {
      const r = await makeRunOnce(NODE3_CODE, { items, trigger: { business_name: 'X' } });
      err = (r && r[0] && r[0].json && r[0].json.pipeline_error) || null;
    }
    catch (e) { err = 'THREW: ' + e.message; }
    out.node3_emptyError = err;
  }

  // =====================================================================
  // NODE 5 tests
  // =====================================================================
  // $('Node 3') is referenced by the real code, so we must patch the mock's
  // named lookup. Simplest faithful approach: run Node 5 with a $ that
  // resolves 'Node 3' to the *actual* Node 3 output.
  function makeRunOnceNode5(items, node3Out) {
    const named = { 'Node 3': node3Out };
    function $(name) {
      return { first: () => ({ json: named[name] || {} }),
               all:  () => [named[name] ? { json: named[name] } : { json: {} }] };
    }
    const $input = { first: () => items[0], all: () => items };
    const helpers = {
      // Real n8n's prepareBinaryData resolves to a base64 STRING (the binary
      // payload), with the filename/mime carried alongside. We return the
      // base64 string here and stash the metadata in a side channel so the
      // assertions can check the filename Gotenberg depends on.
      prepareBinaryData: async (buf, fileName, mimeType) => {
        out.__binMeta = { fileName, mimeType, size: buf.length };
        return buf.toString('base64');
      },
    };
    // n8n's Code node exposes `this.helpers` for prepareBinaryData. Inside a
    // `new Function` body, an arrow function's `this` does not survive the
    // lexical capture the way we need (verified: it resolves to undefined),
    // so the harness rewrites `this.helpers` -> `__helpers` and passes our
    // mock in as a parameter. This is a harness-only substitution; every other
    // line of the Code node runs verbatim, unmodified.
    const body = NODE5_CODE.replace(/this\.helpers/g, '__helpers');
    const fn = new Function('__helpers', 'items', '$', '$input', 'Buffer',
      'return (async () => { ' + body + ' })();');
    return fn(helpers, items, $, $input, Buffer);
  }

  {
    // Build the Node 3 output the real pipeline would produce.
    const node3Items = DOC_REVIEWS.map(t => ({ json: { review: t } }));
    const node3Out = (await makeRunOnce(NODE3_CODE, {
      items: node3Items,
      trigger: { business_name: 'SariSari Store Malate', context: 'Q3 2026' },
    }))[0].json;

    const tagasentiOut = {
      json: { results: STUB_RESULTS.map(([label, scores]) => ({ label, scores })) },
    };

    const r = await makeRunOnceNode5([tagasentiOut], node3Out);
    assert(r.length === 1, 'Node 5 returns 1 item');
    const j = r[0].json;
    const html = Buffer.from(r[0].binary.data, 'base64').toString('utf-8');

    out.stats = j.stats;
    out.verdict = j.verdict;
    out.insightCount = j.insights.length;
    out.recCount = j.stats.total;
    out.binaryFileName = out.__binMeta.fileName;
    out.binaryMime = out.__binMeta.mimeType;
    out.htmlLength = html.length;

    // expected numbers, checkable by hand from STUB_RESULTS
    // 3 Pos (0,4) + 1 Neg(1) + 1 Neu(2) + 1 Neg(3) = Pos 2, Neg 2, Neu 1, n=5
    out.expect_counts = { Positive: 2, Negative: 2, Neutral: 1 };

    // structural checks on the HTML that the doc's checklist cares about
    out.html_checks = {
      startsWithDoctype: html.startsWith('<!DOCTYPE html>'),
      hasA4PageRule: html.includes('@page { size: A4; margin: 0; }'),
      hasDonutGradient: html.includes('conic-gradient('),
      hasGaugeMarker: html.includes('class="marker"'),
      hasPrintBackgroundFriendly: !html.includes('printBackground'),
      closesHtml: html.trim().endsWith('</html>'),
      hasTagaSentiAttribution:
        html.includes('TagaSenti') && html.includes('10.57967/hf/9620'),
      hasBusinessName: html.includes('SariSari Store Malate'),
      hasContext: html.includes('Q3 2026'),
      commentCardCount: (html.match(/class="ccard"/g) || []).length,
      // The low-confidence review (#2) should be flagged
      hasAmbiguousFlag: html.includes('ambiguous'),
    };
  }

  // -- the 3-card cap and the "N more" overflow note -----------------------
  // The report is specified as ONE page. Showing 5 negative + 5 positive
  // cards overflowed A4 and produced a 2-page PDF (observed in the smoke
  // test), so the cap is now 3 and the overflow is stated explicitly.
  {
    // 6 negatives, 3 positives -> 3 shown of each, 3 negatives hidden.
    // Both sides must hit the cap for this test to prove anything.
    const manyNeg = [
      ['Pangit ng service, naghihintay ng 2 hours', 'Negative', 0.99],
      ['Grabe, walang ibang napindot, masama', 'Negative', 0.97],
      ['Bumili ako, niwala sa akin nung wala', 'Negative', 0.95],
      ['Panget ng lugar, maraming daga', 'Negative', 0.93],
      ['Hindi worth it ang bayad', 'Negative', 0.91],
      ['Sobrang bilis, Salamat!', 'Negative', 0.55],   // low conf
      ['Sulit na sulit, uulit ulit', 'Positive', 0.96],
      ['Okay lang naman po', 'Positive', 0.80],
      ['Mabilis ang paghahaini, masarap', 'Positive', 0.78],
    ];
    const reviews9 = manyNeg.map(m => m[0]);
    const results9 = manyNeg.map(([text, label, conf]) => {
      const rival = (1 - conf) / 2;
      const other = label === 'Negative' ? 'Positive' : 'Negative';
      const neu = label === 'Negative' ? 'Neutral' : 'Neutral';
      return { label, scores: { [label]: conf, [other]: rival, [neu]: rival } };
    });
    const node3Out = { reviews: reviews9, business_name: 'Overflow Test', context: '' };
    const tagasentiOut = { json: { results: results9 } };
    const r = await makeRunOnceNode5([tagasentiOut], node3Out);
    const html = Buffer.from(r[0].binary.data, 'base64').toString('utf-8');
    out.cardCap = {
      cardCount: (html.match(/class="ccard"/g) || []).length,
      hasMoreNote: html.includes('class="more"'),
      moreNoteText: (html.match(/…and \d+ more [a-z]+ review[^<]*/) || [''])[0],
      // the totals must still reflect ALL 8, not just the 5 shown
      total: r[0].json.stats.total,
      negCount: r[0].json.stats.counts.Negative,
    };
  }

  // -- TRIGGER SHAPE: Form Trigger vs Webhook --------------------------------
  // Node 1 started as an n8n Form Trigger and was later changed to a Webhook.
  // They put the submitted fields in DIFFERENT PLACES:
  //     Form Trigger -> item.json.business_name
  //     Webhook      -> item.json.body.business_name
  // Node 3 and Node 7 both read the business name, and were written for the
  // trigger shape. With a webhook they silently produced "Unnamed business".
  // These assertions pin BOTH shapes so switching back can never silently
  // regress again - a symptom with no error, which is the worst kind.
  {
    const reviews = ['Ang ganda ng service, sulit na sulit!'];
    const results = [{ label: 'Positive',
                      scores: { Negative: 0.02, Neutral: 0.03, Positive: 0.95 } }];

    // Build the Node 3 output for each trigger shape, via the real Node 3.
    async function node3out(triggerJson) {
      const items = reviews.map(t => ({ json: { review: t } }));
      const r = await makeRunOnce(NODE3_CODE, { items, trigger: triggerJson });
      return r[0].json;
    }

    const shapeA = await node3out({ business_name: 'SariSari Store Malate', context: 'Q3' });
    const shapeB = await node3out({ body: { business_name: 'SariSari Store Malate', context: 'Q3' } });
    out.triggerShapes = {
      flat: shapeA.business_name,
      webhook: shapeB.business_name,
      flatCtx: shapeA.context,
      webhookCtx: shapeB.context,
    };

    // Node 7 derives the output filename from the same field, so pin the
    // expression it actually uses. If this ever reads `form.business_name`
    // without the .body fallback, a webhook upload silently produces a file
    // named 'unnamed-business__...'.
    const slugLine = NODE7_CODE.split(String.fromCharCode(10))
      .find(l => l.includes('const business = slug('));
    out.slugExpr = (slugLine || 'NOT FOUND').trim();
  }

  // -- XSS: a review containing HTML must be escaped, not rendered
  {
    const evil = '<script>alert("xss")</script>';
    const node3Out = { reviews: [evil], business_name: 'X', context: '' };
    const tagasentiOut = {
      json: { results: [{ label: 'Negative', scores: { Negative: 0.9, Neutral: 0.07, Positive: 0.03 } }] },
    };
    const r = await makeRunOnceNode5([tagasentiOut], node3Out);
    const html = Buffer.from(r[0].binary.data, 'base64').toString('utf-8');
    out.xss = {
      rawScriptTagPresent: html.includes('<script>'),
      escapedFormPresent: html.includes('&lt;script&gt;'),
      // and it must still be in the doc, escaped
    };
  }

  // -- length mismatch between reviews and results must throw loudly
  {
    const node3Out = { reviews: ['a', 'b', 'c'], business_name: 'X', context: '' };
    const tagasentiOut = { json: { results: [{ label: 'Positive', scores: { Negative: 0.1, Neutral: 0.1, Positive: 0.8 } }] } };
    let msg = null;
    try { await makeRunOnceNode5([tagasentiOut], node3Out); }
    catch (e) { msg = e.message; }
    out.mismatchError = msg;
  }

  delete out.__binMeta;
  console.log(JSON.stringify(out, null, 2));
})().catch(e => { console.error('HARNESS_ERROR: ' + e.message); console.error(e.stack); process.exit(1); });
"""


def build_harness():
    node3 = get_code("Node 3")
    node5 = get_code("Node 5")
    js = HARNESS_JS
    node7 = get_code("Node 7 - Filename")
    js = js.replace("__NODE3__", json.dumps(node3))
    js = js.replace("__NODE5__", json.dumps(node5))
    js = js.replace("__NODE7__", json.dumps(node7))
    consts = ("const DOC_REVIEWS = " + json.dumps(DOC_TEST_CSV) + ";\n"
              "const STUB_RESULTS = " + json.dumps(STUB_RESULTS) + ";\n")
    return consts + js


def main():
    if not os.path.exists(WORKFLOW_JSON):
        sys.exit(f"FATAL: {WORKFLOW_JSON} missing - run 05_generate_workflow_json.py")
    try:
        subprocess.run(["node", "--version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        sys.exit("FATAL: `node` not on PATH. Install Node.js to test the "
                 "Code nodes, since n8n's Code node is a Node.js sandbox.")

    print("=" * 78)
    print("n8n Code node execution tests (Node 3 + Node 5)")
    print("=" * 78)

    js = build_harness()
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(js)
        path = fh.name
    try:
        proc = subprocess.run(["node", path], capture_output=True, text=True)
    finally:
        os.unlink(path)

    if proc.returncode != 0:
        print("\nHARNESS FAILED - the Code node JavaScript threw:\n")
        print(proc.stdout[-4000:])
        print(proc.stderr[-4000:])
        print("\nThis is a REAL bug in the workflow JS. Fix before importing.")
        return 1

    out = json.loads(proc.stdout)
    passed, failed = [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}"
              + (f"   {detail}" if not cond and detail else ""))

    print("\n-- Node 3: clean + batch --")
    check("returns exactly 1 item (enables one batched call)",
          out["node3_itemCount"] == 1, str(out["node3_itemCount"]))
    check("all 5 CSV rows became reviews",
          out["node3_reviewCount"] == 5, str(out["node3_reviewCount"]))
    check("business_name passed through",
          out["node3_business"] == "SariSari Store Malate", out["node3_business"])
    check("context passed through",
          out["node3_context"] == "Q3 2026", out["node3_context"])
    check("dedupes case+whitespace variants -> 1 review",
          out["dedupe_count"] == 1, str(out["dedupe_count"]))
    check("keeps ORIGINAL text (not the normalised form)",
          out["dedupe_keptText"] == "Sulit na sulit, babalik ulit ako dito pramis",
          out["dedupe_keptText"])
    check("drops empty/whitespace/too-short rows",
          out["shortrow_count"] == 1, str(out["shortrow_count"]))
    for col, got in out["altColumns"].items():
        check(f"reads column alias '{col}'", got == 1, str(got))
    check("empty input returns a rejection (not a throw)",
          bool(out["node3_emptyError"])
          and "No usable reviews" in out["node3_emptyError"]
          and "THREW" not in out["node3_emptyError"],
          str(out["node3_emptyError"]))

    print("\n-- Node 5: stats + insights + HTML --")
    st = out["stats"]
    check("total = 5", st["total"] == 5, str(st["total"]))
    check("counts Positive=2", st["counts"]["Positive"] == 2, str(st["counts"]))
    check("counts Negative=2", st["counts"]["Negative"] == 2, str(st["counts"]))
    check("counts Neutral=1", st["counts"]["Neutral"] == 1, str(st["counts"]))
    # net = (2-2)/5*100 = 0.0  -> "Mixed"
    check("net score = 0 (2 pos - 2 neg of 5)", st["net_score"] == 0,
          str(st["net_score"]))
    check("verdict for net 0 is 'Mixed'",
          "Mixed" in out["verdict"], out["verdict"])
    check("percentages sum to 100",
          abs(st["pct"]["positive"] + st["pct"]["neutral"] + st["pct"]["negative"] - 100) < 0.2,
          str(st["pct"]))
    check("low-confidence review counted (gap 0.054)",
          st["low_confidence"] == 1, str(st["low_confidence"]))
    check("at least 5 insights generated", out["insightCount"] >= 5,
          str(out["insightCount"]))

    print("\n-- Node 5: binary + HTML structure --")
    check("binary filename is index.html (Gotenberg requirement)",
          out["binaryFileName"] == "index.html", out["binaryFileName"])
    check("binary mime is text/html", out["binaryMime"] == "text/html",
          out["binaryMime"])
    check("HTML is substantial (>4KB)", out["htmlLength"] > 4000,
          str(out["htmlLength"]))
    h = out["html_checks"]
    check("starts with DOCTYPE", h["startsWithDoctype"])
    check("has @page A4 rule", h["hasA4PageRule"])
    check("has conic-gradient donut", h["hasDonutGradient"])
    check("has gauge marker element", h["hasGaugeMarker"])
    check("document is closed </html>", h["closesHtml"])
    check("TagaSenti attribution present (Apache-2.0 requirement)",
          h["hasTagaSentiAttribution"])
    check("business name rendered", h["hasBusinessName"])
    check("context rendered", h["hasContext"])
    check("4 comment cards (2 neg + 2 pos)", h["commentCardCount"] == 4,
          str(h["commentCardCount"]))
    check("ambiguous review flagged in report", h["hasAmbiguousFlag"])

    print("\n-- Trigger shape: Form Trigger AND Webhook both work --")
    ts = out["triggerShapes"]
    check("Node 3 reads business_name from a Form Trigger (flat json)",
          ts["flat"] == "SariSari Store Malate", repr(ts["flat"]))
    check("Node 3 reads business_name from a Webhook (json.body)",
          ts["webhook"] == "SariSari Store Malate", repr(ts["webhook"]))
    check("Node 3 reads context from a Webhook too",
          ts["webhookCtx"] == "Q3", repr(ts["webhookCtx"]))
    check("Node 7 slugs the business name rather than falling back",
          "form.business_name" in out["slugExpr"], out["slugExpr"])

    print("\n-- Security: HTML injection via review text --")
    check("raw <script> NOT present in output (escaped)",
          out["xss"]["rawScriptTagPresent"] is False,
          "XSS: review text was rendered as live HTML")
    check("escaped form &lt;script&gt; present",
          out["xss"]["escapedFormPresent"] is True)

    print("\n-- Page-fit: 3-card cap and the overflow note --")
    cc = out["cardCap"]
    check("shows at most 3+3 = 6 comment cards, not 5+5 = 10",
          cc["cardCount"] == 6, str(cc["cardCount"]))
    check("states how many were hidden (no silent truncation)",
          cc["hasMoreNote"], "no 'more' note in the report")
    check("overflow note names the hidden count and the polarity",
          "more negative review" in cc["moreNoteText"].lower(),
          repr(cc["moreNoteText"]))
    check("totals still count ALL reviews, not just the ones shown",
          cc["total"] == 9 and cc["negCount"] == 6,
          f'total={cc["total"]} neg={cc["negCount"]}')

    print("\n-- Safety: results/reviews length mismatch --")
    check("mismatched result count throws a clear error",
          bool(out["mismatchError"]) and "must match" in (out["mismatchError"] or ""),
          str(out["mismatchError"]))

    print("\n" + "=" * 78)
    print(f"RESULT: {len(passed)} passed, {len(failed)} failed")
    if failed:
        for f in failed:
            print(f"  FAILED: {f}")
        print("=" * 78)
        return 1
    print("Both Code nodes execute correctly and produce the expected output.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
