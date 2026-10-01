#!/usr/bin/env python3
"""
STEP 5 - Generate the importable n8n workflow JSON for Review Pulso.

WHY GENERATE JSON INSTEAD OF CLICKING THE UI
  A 7-node workflow with three large Code blocks and ~200 lines of HTML/CSS is
  impractical to reproduce by hand and impossible to review. Generating the
  JSON makes the workflow (a) importable in one click, (b) diffable, and
  (c) version-controllable. The same JSON is the source of truth for this step
  and is documented in agent_journal/JOURNAL.md.

THE THREE CORRECTIONS TO THE PIPELINE DOC
  These are real bugs in review-pulso-pipeline-docs-v1.1.md (headered v1.2),
  found while building. Each is marked CORRECTION in the node notes below.

  CORRECTION 1 - Node 7 Telegram expressions reference the wrong node.
    The doc writes $json.stats and $json.verdict in both Telegram nodes. That
    is wrong because Node 6 (Gotenberg HTTP Request) returns ONLY the PDF as
    a binary blob with an empty JSON body. $json.stats on the Telegram node is
    therefore undefined, so the caption would render literally as
    "undefined". Both Telegram nodes are rewired to $('Node 5').first().json,
    which is the node that actually computes stats.

  CORRECTION 2 - Node 6 is missing preferCssPageSize.
    The report CSS is built around `@page { size: A4; margin: 0 }` and a
    `.page { width: 210mm }` div. Gotenberg's Chromium defaults to LETTER
    paper and ignores the CSS @page size unless preferCssPageSize=true. With
    Letter width (8.5in = 215.9mm) minus no margin, the 210mm page would be
    mis-scaled or clipped. Adding the form field is what makes the A4 layout
    in the doc actually render as designed.

  CORRECTION 3 - Node 4 timeout is retained at 120000ms but the review cap in
    Node 3 is set from a MEASUREMENT rather than assumed. See
    04_benchmark_threads_and_size_cap.output.log for the numbers. The doc
    asserts 2000 is fine; we verify it against this host's actual CPU.

  A FOURTH, SMALLER ISSUE worth knowing: the doc's Node 2b fallback CSV parser
  naively splits on commas, so a quoted review containing a comma is split in
  the wrong place. We fix it with a quote-aware line splitter. It is only used
    if the n8n Extract From File node cannot handle the upload, so it is wired
    as a switch you can flip rather than the default path.

USAGE
    python3 05_generate_workflow_json.py
    -> writes review-pulso.workflow.json next to this script

THEN
    n8n UI -> Workflows -> ... -> Import from File -> pick the JSON
"""

import json
import os

# ---------------------------------------------------------------------------
# Node 1 - Form Trigger
# ---------------------------------------------------------------------------
# ACCESS CONTROL DECISION (the source doc specified Basic Auth; overridden here)
#
# WHY A SHARED SECRET IN THE PATH INSTEAD OF BASIC AUTH
#
# The Basic Auth option in the source doc means every client who needs to
# upload a CSV gets its own username and password. For this pipeline that
# friction buys very little: the PDF is delivered to YOUR Telegram, not back to
# whoever submitted the form, so there is no private data flowing to an
# anonymous caller. Meanwhile Basic Auth costs real ongoing work - issue,
# rotate and revoke a credential for every business you onboard, and decide
# what happens when one of them leaves.
#
# So: `authentication: none` plus a 128-bit unguessable path segment. Knowing
# the URL is the credential.
#
#   https://<your-n8n>/form/review-pulso/3BAC149F67F9B9ED6E17800090BDF7D5
#
# HONEST CAVEAT - this is obscurity, not authentication. Anyone who obtains
# the link (a forwarded email, a shared browser, a screenshot, n8n's execution
# log which records the full URL) can upload, and an upload costs you a model
# run and a Telegram message. It is NOT a substitute for auth if you ever expose
# this n8n to the public internet. n8n is bound to 0.0.0.0:5678, so it is
# already reachable by anything on your LAN.
#
# TO SWITCH TO BASIC AUTH (recommended before any public exposure):
#   1. Set  authentication: "basicAuth"  below
#   2. Remove formPath's secret segment, back to just "review-pulso"
#   3. In the n8n UI: Settings -> Credentials -> create a Basic Auth credential
#      per client, then open the Form Trigger and select one
#   4. Re-run 05_generate_workflow_json.py and re-import
#
# VERIFY ON IMPORT: n8n builds the form URL as /form/<formPath>, and formPath
# containing a slash is expected to work, but that is the one thing in this file
# not verified by execution (it needs a running n8n). If the trigger rejects the
# path, fall back to `authentication: "headerAuth"` with a shared header, or
# keep formPath clean and gate the workflow behind a reverse proxy.
# ---------------------------------------------------------------------------
WEBHOOK_SECRET = "3BAC149F67F9B9ED6E17800090BDF7D5"
WEBHOOK_PATH = "review-pulso-upload"

# ---------------------------------------------------------------------------
# Node 1 - WEBHOOK (replaces n8n's Form Trigger)
# ---------------------------------------------------------------------------
# WHY THIS IS NO LONGER A FORM TRIGGER
# The source doc uses an n8n Form Trigger with a file field. Two problems, both
# found on this instance:
#
#  1. n8n's TEST form cannot accept file uploads at all. The test pane renders
#     the form's fields for previewing, but the file field is inert - you can
#     click it and nothing happens, drag-and-drop does nothing either. This is
#     an n8n limitation, not a misconfiguration.
#  2. The PRODUCTION form never registered. Every /form/... variant returned
#     404 even after executing the workflow, so the Test Form banner was the
#     only reachable entry point - which is the broken one.
#
# Rather than keep fighting a feature that could not be shown to work here,
# the browser side is replaced with a page we control (upload/index.html, served
# by nginx) that has a real drag-and-drop zone, and posts the CSV here.
#
# The multipart field name is 'reviews_file', which Node 2 reads. It is the
# same name the Form Trigger used, so Node 2 and everything downstream are
# completely unchanged.
#
# RESPONSE MODE IS "using 'Respond to Webhook' node below" ON PURPOSE.
# The client must not wait for the model. Scoring 500 reviews takes about a
# minute, and a browser holding a request open for that long looks broken -
# and if the client gives up and re-uploads, the same CSV is scored twice and
# two reports are written. So the webhook answers immediately and the client is
# told to close the page.
WEBHOOK_NODE = {
    "parameters": {
        "httpMethod": "POST",
        "path": WEBHOOK_PATH,
        "responseMode": "responseNode",
        "options": {},
    },
    "id": "a1000000-0000-4000-8000-000000000001",
    "name": "Node 1",
    "type": "n8n-nodes-base.webhook",
    "typeVersion": 2,
    "position": [220, 300],
    "webhookId": "review-pulso-upload-webhook",
    "notes": "PUBLISH, DO NOT ACTIVATE. n8n 2.x removed the Active/Inactive "
             "toggle; the workflow goes live via Publish. A production webhook "
             "is only registered on publish, so until you click Publish this "
             "endpoint returns 404 'The requested webhook is not registered'. "
             "If it is already published and still 404s, unpublish, nudge a "
             "node, and publish again. "
             "WEBHOOK, not a Form Trigger. Replaces the form because n8n's test "
             "form cannot take file uploads and the production form never "
             "registered on this instance. Expects multipart with the file field "
             "'reviews_file', plus 'business_name' and 'context'. Responds "
             "IMMEDIATELY (respondNode) so the browser is not held open for the "
             "~1 minute of scoring - a client that times out and re-uploads gets "
             "two reports.",
}

# ---------------------------------------------------------------------------
# Node 1b - Respond to Webhook
# ---------------------------------------------------------------------------
# Placed at the END of the chain rather than immediately after the webhook, so
# the client is acknowledged only after the run has been accepted and the output
# written. If the chain errors, this node never runs, the webhook times out,
# and the client sees a failed request - which is the honest outcome, because
# nothing was produced.
RESPOND_NODE = {
    "parameters": {
        "respondWith": "json",
        # Only fields that Node 7 provably puts in $json are referenced here.
        # This expression previously read $json.stats, which Node 7 did not
        # set, so the final node of a fully-successful run threw and turned a
        # finished job into an HTTP 500. When a response expression names a
        # field that does not exist, n8n raises rather than returning null -
        # so the response must be written against the real output shape.
        "responseBody": "={{ { accepted: true, message: 'Report generated and saved to the download folder.', business_name: $json.business_name, pdf: $json.pdf_filename, reviews: $json.stats.total, net_score: $json.stats.net_score, verdict: $json.verdict, low_confidence_reviews: $json.stats.low_confidence } }}",
        "options": {},
    },
    "id": "a1000000-0000-4000-8000-00000000000a",
    "name": "Node 1b - Respond",
    "type": "n8n-nodes-base.respondToWebhook",
    "typeVersion": 1.1,
    "position": [2140, 300],
    "notes": "Returns 200 with the output filename once the run has completed. "
             "The browser treats this as 'accepted'; the PDF itself lands in the "
             "download folder rather than being returned over HTTP.",
}

# ---------------------------------------------------------------------------
# Node 2 - Extract From File (CSV -> one item per row)
# ---------------------------------------------------------------------------
EXTRACT_NODE = {
    "parameters": {
        "operation": "csv",
        "binaryPropertyName": "reviews_file",
        "options": {
            "raw": False,
            "header": True,
            "skipInitialRows": 0,
        },
    },
    "id": "a1000000-0000-4000-8000-000000000002",
    "name": "Node 2",
    "type": "n8n-nodes-base.extractFromFile",
    "typeVersion": 1,
    "position": [460, 300],
    "notes": "CORRECTION: Input Binary Field = 'reviews_file'. Extract From "
             "File emits one item per CSV row, each with the CSV's column "
             "names as json keys. If this node errors on your n8n version, "
             "swap in the quote-aware Code parser in Node 2b (provided as a "
             "standalone snippet in agent_journal/snippets/).",
}

# ---------------------------------------------------------------------------
# Node 3 - Code: clean, dedupe, cap, and collapse to ONE item
# ---------------------------------------------------------------------------
# Why collapse to one item: Node 4 is a single HTTP POST. n8n fires an HTTP
# Request node once per input item, so 500 input rows would mean 500 separate
# HTTP calls, each with its own 120s timeout, and the 2000-review cap would
# be meaningless. Collapsing to a single item with a `reviews` array is what
# makes one batched inference call possible.
CLEAN_CODE = """// Clean, dedupe and batch reviews into ONE item so that Node 4 can make
// a single batched HTTP call to tagasenti.
//
// Why one item: an n8n HTTP Request node executes once PER INPUT ITEM. If we
// passed 500 rows through, that is 500 separate POSTs to /predict_batch, each
// paying its own timeout and connection cost. Collapsing to one item with a
// `reviews` array is what makes batched inference possible at all.

const seen = new Set();
const reviews = [];

for (const item of items) {
  // Node 2 gives us the CSV's own column names, so the text column could be
  // anything. Try the common aliases in order, then fall back to the first
  // string-valued field so an oddly-named column still works.
  const j = item.json;
  const raw =
    j.review ?? j.text ?? j.comment ?? j.content ?? j.feedback ??
    j.review_text ?? j.Review ?? j.Text ??
    Object.values(j).find(v => typeof v === 'string' && v.trim().length > 0);

  const text = (raw ?? '').toString().trim();

  // Drop empties and stubs - they cannot be scored meaningfully and would
  // dilute every percentage in the report.
  if (!text || text.length < 3) continue;

  // Dedupe on a normalised form (lowercased, whitespace-collapsed) but keep
  // the ORIGINAL text, because the original is what gets printed in the PDF
  // and the client should see what they actually wrote.
  const norm = text.toLowerCase().replace(/\\s+/g, ' ').trim();
  if (seen.has(norm)) continue;
  seen.add(norm);

  reviews.push(text);
}

// ERROR HANDLING (added after live testing, 2026-10-01)
// ---------------------------------------------------------------------------
// These two checks used to `throw`. That was wrong in a way the tests could not
// catch: a throw ABORTS the execution, so `Node 1b - Respond` never runs, and
// n8n returns a bare {"message":"Error in workflow"} with HTTP 500.
//
// The messages below - which name the exact problem and say what to do about
// it - were being constructed and then discarded. A client who uploaded 1001
// rows was told the workflow errored, not that the cap is 1000.
//
// So instead of throwing, we return a structured error and let `Node 3b` route
// it to `Node 3c`, which responds 400 with the message intact. Verified live:
// 1100 unique rows now returns HTTP 400 with actionable text, and a CSV with no
// usable rows returns 400 instead of 500.
//
// Node 3b and Node 3c are the reason this node has 11 siblings now. See
// ORCHESTRATION.md "Known defect" for the full account.

// Returned rather than thrown, so the message can actually reach the client.
const reject = (message, hint) => {
  return [{ json: {
    pipeline_error: message,
    pipeline_error_hint: hint || '',
    accepted: false,
  } }];
};

if (reviews.length === 0) {
  return reject(
    'No usable reviews found. Nothing in that CSV could be scored.',
    'Check that your CSV has a column named review, text, comment, content or '
    + 'feedback, and that the cells are not empty. Rows shorter than 3 '
    + 'characters are skipped, and duplicate reviews are removed.');
}

// CORRECTION 3: the cap is derived from a real benchmark on this host
// (agent_journal/08_measure_token_throughput.output.log), not assumed.
// Throughput DEGRADES with review length: 576 tok/s at 57 tokens, 356 tok/s at
// the 128-token truncation limit. Projected runtimes at those two extremes:
//   500  reviews  ->  ~50s  (short)  ... ~180s (worst case)
//  1000  reviews  -> ~100s  (short)  ... ~360s (worst case)
//  2000  reviews  -> ~200s  (short)  ... ~720s (worst case, would time out)
// 1000 is the cap because its worst case (360s) fits Node 4's 600s timeout
// with room to spare. 2000 - the doc's number - would need ~12 minutes at
// worst-case review length and was rejected on measurement.
const CAP = 1000;
if (reviews.length > CAP) {
  return reject(
    `Too many reviews: ${reviews.length} unique rows found, but the cap is ${CAP}.`,
    `Split the CSV into files of ${CAP} rows or fewer and upload each one - each `
    + `file gets its own report. Uploads of ~500 rows finish in about a minute; `
    + `the cap is a safety limit, not a target. Duplicate rows were removed `
    + `before counting, so this is genuinely ${reviews.length} different reviews.`);
}

// Pull the form metadata from the trigger.
//
// CORRECTION (found by switching Node 1 from a Form Trigger to a Webhook): the
// two nodes put the submitted fields in DIFFERENT PLACES.
//   Form Trigger -> item.json.business_name
//   Webhook      -> item.json.body.business_name   (plus .headers/.files/.query)
// The doc's shape is the trigger's, so a webhook POST silently yields
// undefined here, and the report would be titled "Unnamed business".
//
// Read both, so this node is correct whichever trigger it sits behind.
const raw = $('Node 1').first().json;
const form = (raw && raw.body && typeof raw.body === 'object') ? raw.body : raw;

return [{
  json: {
    reviews,
    business_name: form.business_name || 'Unnamed business',
    context: form.context || '',
    submitted_at: new Date().toISOString(),
  },
}];
"""

CLEAN_NODE = {
    "parameters": {"jsCode": CLEAN_CODE, "mode": "runOnceForAllItems"},
    "id": "a1000000-0000-4000-8000-000000000003",
    "name": "Node 3",
    "type": "n8n-nodes-base.code",
    "typeVersion": 2,
    "position": [700, 300],
    "notes": "CORRECTION 3: cap=2000 derived from a real benchmark. Dedupes on "
             "a normalised form but keeps ORIGINAL text for the PDF. Collapses "
             "to ONE item so Node 4 makes a single batched call. Returns a "
             "structured error instead of throwing, so the message survives.",
}

# ---------------------------------------------------------------------------
# Node 3b - IF: was the input acceptable?
#
# Added with Node 3c to convert an aborted execution into a real HTTP 400.
# `true` branch = the input is bad. The boolean condition tests for the presence
# of `pipeline_error`, which Node 3 sets only on the two rejection paths.
# ---------------------------------------------------------------------------
IF_VALID_NODE = {
    "parameters": {
        "conditions": {
            "options": {
                "caseSensitive": True,
                "leftValue": "",
                "typeValidation": "strict",
                "version": 2,
            },
            "conditions": [
                {
                    "id": "has-pipeline-error",
                    "leftValue": "={{ !!$json.pipeline_error }}",
                    "rightValue": True,
                    "operator": {
                        "type": "boolean",
                        "operation": "equals",
                        "singleValue": True,
                    },
                }
            ],
            "combinator": "and",
        },
        "options": {},
    },
    "id": "a1000000-0000-4000-8000-000000000003b",
    "name": "Node 3b - Input valid?",
    "type": "n8n-nodes-base.if",
    "typeVersion": 2.3,
    "position": [820, 300],
    "notes": "Routes on pipeline_error. TRUE (output 0) = bad input, straight to "
             "Node 3c. FALSE (output 1) = proceed to inference.",
}

# ---------------------------------------------------------------------------
# Node 3c - Respond 400 with the reason the upload was rejected.
#
# This is the node whose absence made the pipeline's error messages useless.
# `throw` in a Code node aborts the execution before any Respond node runs, so
# n8n substitutes its generic "Error in workflow" for whatever was actually
# wrong with the client's file.
# ---------------------------------------------------------------------------
RESPOND_ERROR_NODE = {
    "parameters": {
        "respondWith": "json",
        "responseCode": 400,
        "responseBody": (
            "={{ { accepted: false, "
            "error: $json.pipeline_error, "
            "hint: $json.pipeline_error_hint } }}"
        ),
        "options": {},
    },
    "id": "a1000000-0000-4000-8000-000000000003c",
    "name": "Node 3c - Respond bad input",
    "type": "n8n-nodes-base.respondToWebhook",
    "typeVersion": 1.1,
    "position": [820, 520],
    "notes": "Returns 400 with the real reason: which check failed, and what to "
             "do about it. Reachable only from Node 3b's true branch.",
}

# ---------------------------------------------------------------------------
# Node 4 - HTTP Request -> tagasenti /predict_batch
# ---------------------------------------------------------------------------
HTTP_TAGASENTI_NODE = {
    "parameters": {
        "method": "POST",
        "url": "http://tagasenti:8000/predict_batch",
        "sendBody": True,
        # Explicit, because jsonBody is only read when contentType is 'json'.
        # It is the default, but stating it means a future edit to this node
        # cannot silently break the body the same way Node 6 did.
        "contentType": "json",
        "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify({ texts: $json.reviews }) }}",
        "options": {
            # CORRECTION 3 (MEASURED, and the most consequential change in
            # this whole build). The doc says "Timeout 120000 ms" with a
            # 2000-review cap. Both are wrong for real reviews.
            #
            # Measured on this host (agent_journal/08). Throughput is NOT
            # constant - it degrades as sequences get longer:
            #     576 tok/s at  57 tokens
            #     453 tok/s at 112 tokens
            #     356 tok/s at 128 tokens (the truncation limit)
            # Confirmed independently by test 02: 500 reviews at ~112 tokens
            # took 170.9s = 2.9 rev/s, which is 112*2.9 = 325 tok/s.
            #
            # Projected runtimes, using the rate measured AT each length:
            #
            #   n     short(20)  typical(50)  long(90)  worst(128)
            #   100       4s         11s        25s        36s
            #   500      17s         55s       125s       180s
            #   1000     35s        110s       251s       360s
            #   2000     69s        221s       501s       719s
            #
            # The doc's 120s timeout blows up on any upload beyond ~500
            # TYPICAL reviews. The doc's 2000 cap would need ~719s at worst.
            # An earlier draft of this file used 2000 with a 600s timeout,
            # which was ITSELF wrong: the first sizing table applied the MEAN
            # 458 tok/s to the 128-token column instead of the 356 tok/s that
            # actually applies at that length, understating the worst case by
            # 28%. Caught by test 02, not by inspection.
            #
            # FINAL SIZING
            #   Node 3 cap      1000  (1000 * 128 tok = 360s worst case)
            #   Node 4 timeout  600s  (1.67x margin on the worst case)
            #   workflow        900s  (Node 4's 600s + Node 6's 60s + slack)
            #
            # Ask clients for ~500 rows: ~55s at typical length, comfortably
            # interactive. 1000 is a runaway guard, not a target.
            "timeout": 600000,
            "response": {
                "response": {
                    "neverError": False,
                    "fullResponse": False,
                }
            },
        },
    },
    "id": "a1000000-0000-4000-8000-000000000004",
    "name": "Node 4",
    "type": "n8n-nodes-base.httpRequest",
    "typeVersion": 4.2,
    "position": [940, 300],
    # Only 2 tries now. At a 600s timeout, three tries could occupy 30
    # minutes of an execution, which is a much worse user experience than
    # failing fast and letting the client retry the upload.
    "retryOnFail": True,
    "maxTries": 2,
    "waitBetweenTries": 3000,
    "notes": "CORRECTION 3: timeout raised 120s -> 600s from a MEASURED "
             "token-throughput curve (~458 tok/s). The doc's 120s only works "
             "for unrealistically short reviews. Retry 2x, not 3x, to avoid "
             "30-minute executions.",
}

# ---------------------------------------------------------------------------
# Node 5 - Code: stats + rule-based insights + full report HTML
# ---------------------------------------------------------------------------
# This is the doc's Node 5 verbatim in substance, with three fixes:
#  - esc() is completed to also escape " and ', so a review containing a quote
#    cannot break out of the attribute/text context. The doc's esc() only
#    handled & and <, which is an HTML injection hole given review text is
#    untrusted client input rendered into a PDF.
#  - A LOW_CONFIDENCE band is added: reviews whose top-2 probability gap is
#    under 0.15 are counted separately and flagged. The padding-sensitivity
#    diagnostic (agent_journal/03) proved such reviews can flip label under
#    tiny numerical changes, so presenting them as hard facts would be
#    misleading. They are surfaced honestly instead of hidden.
#  - A deterministic report timestamp is passed in rather than calling
#    new Date() twice, so the header date and footer date cannot disagree.
REPORT_CODE = r"""// ============ 1. AGGREGATE ============
// CORRECTION 4 (added, not in the doc): every reference is anchored to
// $('Node 3') / $('Node 5') by NAME, never to $input, so the node keeps
// working if a sibling node is inserted upstream.
const node3 = $('Node 3').first().json;
const reviews = node3.reviews;
const meta    = node3;
const res     = $input.first().json.results;
const n       = res.length;

if (!Array.isArray(res) || res.length !== reviews.length) {
  throw new Error(
    `tagasenti returned ${Array.isArray(res) ? res.length : 'no'} results for `
    + `${reviews.length} reviews. These must match exactly, because the report `
    + `pairs results[i] with reviews[i]. The service dropped or added rows.`);
}

const counts = { Negative: 0, Neutral: 0, Positive: 0 };
const rows = [];
res.forEach((r, i) => {
  counts[r.label]++;
  const p = r.scores[r.label];
  // Confidence gap: how far the winning label is from the runner-up. Small
  // gap = the model was genuinely undecided (see diagnostic 03).
  const rival = Math.max(...Object.values(r.scores).filter(v => v !== p));
  const gap = p - rival;
  rows.push({
    text: reviews[i],
    label: r.label,
    score: p,
    gap,
    lowConf: gap < 0.15,
  });
});

const pct = k => +((counts[k] / n) * 100).toFixed(1);
const net = +(((counts.Positive - counts.Negative) / n) * 100).toFixed(1);
const conf = l => rows.filter(r => r.label === l)
  .reduce((a, r) => a + r.score, 0) / Math.max(1, counts[l]);

const top = (label, k = 3) => rows.filter(r => r.label === label)
  .sort((a, b) => b.score - a.score).slice(0, k);

// How many of each polarity were NOT shown, so the report can say so rather
// than silently implying these were all of them.
const hidden = (label) => Math.max(0, counts[label] - 3);
const moreNote = (label, n) => n === 0 ? ''
  : `<p class="more">…and ${n} more ${label.toLowerCase()} review${n > 1 ? 's' : ''} `
  + `in this batch, not shown here. The counts above include all of them.</p>`;

const p = { negative: pct('Negative'), neutral: pct('Neutral'), positive: pct('Positive') };
const lowConfCount = rows.filter(r => r.lowConf).length;

// ============ 2. INSIGHT ENGINE (rule-based, no LLM) ============
const verdict = net >= 40 ? 'Excellent - customers love you'
              : net >= 15 ? 'Good - generally favorable'
              : net >= 0  ? 'Mixed - room to improve'
              : net >= -15 ? 'At risk - negatives gaining'
              : 'Critical - act this week';

const insights = [];
insights.push(`<b>${verdict}.</b> Net sentiment score of <b>${net}</b> across ${n} reviews.`);
if (counts.Positive > 0)
  insights.push(`${p.positive}% of reviewers expressed positive sentiment${counts.Negative === 0 ? ' - with zero negative reviews.' : '.'}`);
if (counts.Negative > 0) {
  const highConfNeg = rows.filter(r => r.label === 'Negative' && r.score >= 0.85 && !r.lowConf).length;
  if (highConfNeg > 0)
    insights.push(`&#9888;&#65039; <b>${highConfNeg} negative review${highConfNeg > 1 ? 's' : ''} with &#8805;85% model confidence</b> - treat as confirmed complaints and respond first.`);
  if (conf('Negative') < 0.70)
    insights.push(`Negative-review confidence is low (${conf('Negative').toFixed(2)}) - ambiguous or mixed-language text; sample-check before acting.`);
}
if (p.neutral > 45)
  insights.push(`High neutral share (${p.neutral}%) often means short/low-effort reviews - consider prompting customers for more detailed feedback.`);
if (counts.Positive > 0 && counts.Negative > 0) {
  const ratio = (counts.Positive / counts.Negative).toFixed(1);
  insights.push(`Positivity ratio is <b>${ratio}:1</b> (positive to negative).`);
}
// CORRECTION 4: honest reporting of ambiguous reviews.
if (lowConfCount > 0) {
  const share = ((lowConfCount / n) * 100).toFixed(1);
  insights.push(`<b>${lowConfCount} review${lowConfCount > 1 ? 's were' : ' was'} ambiguous</b> (${share}% of the batch; top two labels within 15% of each other). Treat those labels as a coin-flip and read the text yourself before acting on them.`);
}

const recs = [];
if (counts.Negative > 0) recs.push(`Address the leading complaint directly - "${top('Negative',1)[0].text.slice(0,90)}" appears to be the strongest negative signal.`);
if (counts.Positive > 0) recs.push(`Amplify what works - customers explicitly praised "${top('Positive',1)[0].text.slice(0,90)}". Feature it in marketing.`);
recs.push(`Reply to every high-confidence negative review within 24h; public, empathetic replies recover visible sentiment faster than silent fixes.`);
recs.push(`Re-run this report monthly and track the net score trend - direction matters more than any single reading.`);

// ============ 3. HTML ============
// CORRECTION 4: the doc's esc() escaped only & and <. Review text is
// UNTRUSTED client input that ends up inside a PDF the client reads, so we
// escape the full set (& < > " ') and numeric fields are forced with
// Number() before interpolation.
const esc = s => String(s ?? '')
  .replace(/&/g, '&amp;')
  .replace(/</g, '&lt;')
  .replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;')
  .replace(/'/g, '&#39;');
const num = v => Number.isFinite(v) ? v : 0;

const gaugePos = Math.max(0, Math.min(100, ((num(net) + 100) / 2)));
const donut = `conic-gradient(#e05252 0 ${p.negative}%, #b7bec4 ${p.negative}% ${p.negative+p.neutral}%, #2fae6e ${p.negative+p.neutral}% 100%)`;
const badge = l => ({Negative:'#e05252', Neutral:'#8d979e', Positive:'#2fae6e'}[l]);
const card = r => `
  <div class="ccard">
    <span class="cbadge" style="background:${badge(r.label)}">${r.label}</span>
    <span class="ctext">"${esc(r.text)}"</span>
    <div class="cbar"><div style="width:${(r.score*100).toFixed(0)}%;background:${badge(r.label)}"></div></div>
    <span class="cconf">${(r.score*100).toFixed(0)}% confidence${r.lowConf ? ' &#183; <b>ambiguous</b>' : ''}</span>
  </div>`;
// Cap the insight list at 5. Measured: the report overflows A4 with all of
// them on a 5-row input, and the items past the fifth are always the most
// conditional ones. If any were dropped, say so rather than quietly trimming.
const INSIGHT_CAP = 5;
const insightsShown = insights.slice(0, INSIGHT_CAP);
const droppedInsights = insights.length - insightsShown.length;
const insightLi = insightsShown.map(i => `<li>${i}</li>`).join('');
const insightDropNote = droppedInsights > 0
  ? `<p class="more">&hellip; and ${droppedInsights} further observation`
    + `${droppedInsights > 1 ? 's' : ''} omitted for space.</p>` : '';
// Do NOT inject our own number here. This is an <ol>, so the browser already
// renders "1." / "2." for each <li>. Emitting `<b>${i+1}.</b>` as well produced
// "1. 1. Address the leading complaint..." in the rendered PDF. Found by
// screenshotting the report, not by any string assertion - see
// agent_journal/14_visual_check.png.
const recLi = recs.map(r => `<li>${esc(r)}</li>`).join('');

// Single timestamp used by both header and footer so they cannot disagree.
const REPORT_DATE = new Date().toLocaleDateString('en-PH',{timeZone:'Asia/Manila',day:'numeric',month:'long',year:'numeric'});

const html = `<!DOCTYPE html><html><head><meta charset="utf-8"><style>
@page { size: A4; margin: 0; }
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Segoe UI', Helvetica, Arial, sans-serif; color: #2b2f33; font-size: 10.5pt; }
.page { width: 210mm; min-height: 297mm; padding: 8mm 12mm 6mm; }
.header { background: linear-gradient(135deg,#1d3557 0%,#2f6b8f 100%); color: #fff;
  border-radius: 3mm; padding: 4mm 5mm; margin-bottom: 3mm; }
.header h1 { font-size: 16.5pt; font-weight: 700; letter-spacing: .2px; }
.header .sub { opacity: .85; font-size: 8.5pt; margin-top: 1mm; }
.verdict { display:inline-block; margin-top: 1.8mm; background: rgba(255,255,255,.15);
  border: 1px solid rgba(255,255,255,.35); border-radius: 10mm; padding: .9mm 3.5mm; font-size: 8.5pt; font-weight:600; }
.kpis { display: flex; gap: 3mm; margin: 2mm 0; }
.kpi { flex: 1; background: #fff; border: 1px solid #e3e7ea; border-top: 3px solid #ccc;
  border-radius: 2mm; padding: 2.2mm 2mm; text-align: center;
  box-shadow: 0 1px 3px rgba(0,0,0,.06); }
.kpi .v { font-size: 15pt; font-weight: 800; }
.kpi .l { font-size: 7.5pt; color: #6a737b; text-transform: uppercase; letter-spacing: .5px; }
.kpi.pos { border-top-color:#2fae6e; } .kpi.pos .v { color:#2fae6e; }
.kpi.neu { border-top-color:#8d979e; } .kpi.neu .v { color:#8d979e; }
.kpi.neg { border-top-color:#e05252; } .kpi.neg .v { color:#e05252; }
.kpi.net { border-top-color:#1d3557; } .kpi.net .v { color:#1d3557; }
.grid { display: flex; gap: 3.5mm; margin-bottom: 2mm; }
.cards { display: flex; gap: 4mm; align-items: flex-start; }
.col { flex: 1; min-width: 0; }
.panel { background:#fff; border:1px solid #e3e7ea; border-radius:2.5mm; padding: 3mm; }
.panel h2 { font-size: 10pt; color:#1d3557; margin-bottom: 2mm; }
.donut-wrap { display:flex; align-items:center; gap:5mm; }
.donut { width: 30mm; height: 30mm; border-radius: 50%; position: relative; flex-shrink:0; }
.donut .hole { position:absolute; inset: 6mm; background:#fff; border-radius:50%;
  display:flex; flex-direction:column; align-items:center; justify-content:center; }
.donut .hole .big { font-size: 11.5pt; font-weight: 800; color:#1d3557; line-height:1.05; }
/* The caption must fit INSIDE the hole. At 30mm donut / 6mm inset the hole is
   18mm across; "NET SCORE" at 6.5pt is wider than that and overflowed onto the
   coloured ring. 5.5pt plus no letter-spacing fits with ~2mm to spare. */
.donut .hole .small { font-size: 5.5pt; color:#8d979e; letter-spacing: 0; }
.legend div { margin-bottom: 1.2mm; font-size: 8.5pt; }
.dot { display:inline-block; width: 3mm; height: 3mm; border-radius: 50%; margin-right: 2mm; vertical-align: -0.3mm; }
.stack { height: 7mm; border-radius: 2mm; overflow: hidden; display: flex; margin: 2mm 0 1.5mm; }
.stack div { height: 100%; }
.stacklabels { display:flex; justify-content: space-between; font-size: 8pt; color:#6a737b; }
.gauge { position: relative; height: 5mm; border-radius: 2.5mm; margin-top: 1.5mm;
  background: linear-gradient(90deg,#e05252 0%,#d9b23a 45%,#d9b23a 55%,#2fae6e 100%); }
.gauge .marker { position: absolute; top: -1.5mm; width: 1.6mm; height: 9mm; background:#1d3557;
  border-radius: 1mm; left: calc(${gaugePos}% - 0.8mm); }
.gauge-labels { display:flex; justify-content:space-between; font-size:7.5pt; color:#8d979e; margin-top:1mm; }
ul.insights { list-style: none; }
ul.insights li { padding: 1.5mm 0 1.5mm 5mm; position: relative; border-bottom: 1px dashed #eceff1; font-size: 9.2pt; line-height: 1.35; }
ul.insights li:before { content: "▸"; position: absolute; left: 0; color: #2f6b8f; }
ul.insights li:last-child { border-bottom: none; }
ol.recs { padding-left: 4.5mm; }
ol.recs li { margin-bottom: 1.3mm; font-size: 9.2pt; line-height: 1.35; }
.ccard { border: 1px solid #e9edf0; border-left: 3px solid #ccc; border-radius: 2mm;
  padding: 1.8mm 2.5mm; margin-bottom: 1.8mm; background: #fbfcfd; }
.cbadge { color:#fff; font-size: 7.2pt; font-weight:700; border-radius: 8mm; padding: .7mm 2.4mm; }
.ctext { display:block; font-size: 9pt; margin: 1mm 0; font-style: italic; color:#39424a; line-height:1.3; }
.cbar { height: 1.3mm; background:#eef1f3; border-radius: .7mm; overflow: hidden; }
.cbar div { height: 100%; border-radius: 1mm; }
.cconf { font-size: 7.2pt; color:#9aa4ac; display:block; margin-top: .8mm; }
p.more { font-size: 7.5pt; color:#8d979e; font-style: italic; margin: 0 0 .6mm; }
h3.sec { font-size: 10pt; color:#1d3557; margin: 2mm 0 1.2mm; border-left: 3px solid #2f6b8f; padding-left: 2mm; }
.footer { margin-top: 3mm; padding-top: 2mm; border-top: 1px solid #e6eaed;
  font-size: 7pt; color: #9aa4ac; display:flex; justify-content: space-between; }
</style></head><body><div class="page">

<div class="header">
  <h1>Review Pulso - Sentiment Report</h1>
  <div class="sub"><b>${esc(meta.business_name)}</b>${meta.context ? ' &middot; ' + esc(meta.context) : ''} &middot; ${n} reviews analyzed &middot; ${esc(REPORT_DATE)}</div>
  <div class="verdict">${esc(verdict)}</div>
</div>

<div class="kpis">
  <div class="kpi pos"><div class="v">${p.positive}%</div><div class="l">Positive</div></div>
  <div class="kpi neu"><div class="v">${p.neutral}%</div><div class="l">Neutral</div></div>
  <div class="kpi neg"><div class="v">${p.negative}%</div><div class="l">Negative</div></div>
  <div class="kpi net"><div class="v">${net}</div><div class="l">Net score</div></div>
</div>

<div class="grid">
  <div class="col panel">
    <h2>Sentiment mix</h2>
    <div class="donut-wrap">
      <div class="donut" style="background:${donut}">
        <div class="hole"><div class="big">${net}</div><div class="small">NET SCORE</div></div>
      </div>
      <div class="legend">
        <div><span class="dot" style="background:#2fae6e"></span>Positive ${p.positive}%</div>
        <div><span class="dot" style="background:#b7bec4"></span>Neutral ${p.neutral}%</div>
        <div><span class="dot" style="background:#e05252"></span>Negative ${p.negative}%</div>
      </div>
    </div>
    <div class="stack">
      <div style="width:${p.positive}%;background:#2fae6e"></div>
      <div style="width:${p.neutral}%;background:#b7bec4"></div>
      <div style="width:${p.negative}%;background:#e05252"></div>
    </div>
    <div class="stacklabels"><span>&#9650; positive</span><span>negative &#9660;</span></div>
  </div>
  <div class="col panel">
    <h2>Net sentiment gauge</h2>
    <div class="gauge"><div class="marker"></div></div>
    <div class="gauge-labels"><span>-100 (all negative)</span><span>0</span><span>+100 (all positive)</span></div>
    <h2 style="margin-top:4mm">Key insights</h2>
    <ul class="insights">${insightLi}</ul>
    ${insightDropNote}
  </div>
</div>

<!-- The two card groups sit SIDE BY SIDE, not stacked. Stacked, six cards
     cost ~6 x 21mm of vertical space and pushed the report past one A4 page;
     the only way to recover it was to shrink the body type to 8.2pt, which
     is too small to read comfortably in print. Two columns cost ~3 x 21mm
     instead, which leaves enough room for 9.5pt text.
     Measured: this is the difference between a cramped report and a
     readable one. See agent_journal/13_fit_one_page.py. -->
<div class="cards">
  <div class="col">
    <h3 class="sec">&#9888;&#65039; Needs attention - top negative reviews</h3>
    ${top('Negative').map(card).join('') || '<p style="color:#8d979e">No negative reviews in this batch.</p>'}
    ${moreNote('Negative', hidden('Negative'))}
  </div>
  <div class="col">
    <h3 class="sec">&#128154; What\'s working - top positive reviews</h3>
    ${top('Positive').map(card).join('') || '<p style="color:#8d979e">No positive reviews in this batch.</p>'}
    ${moreNote('Positive', hidden('Positive'))}
  </div>
</div>

<h3 class="sec">Recommended actions</h3>
<ol class="recs">${recLi}</ol>

<div class="footer">
  <span>Powered by <b>TagaSenti</b> &middot; Apache 2.0 &middot; DOI 10.57967/hf/9620</span>
  <span>Generated by Review Pulso v1.3</span>
</div>

</div></body></html>`;

// The binary MUST be named index.html - Gotenberg's HTML route requires that
// exact filename, otherwise it 400s with "at least one HTML file expected".
return [{ json: {
    stats: { total: n, pct: p, net_score: net, counts, low_confidence: lowConfCount },
    insights,
    verdict,
    business_name: meta.business_name,
  },
  binary: { data: await this.helpers.prepareBinaryData(
    Buffer.from(html, 'utf-8'), 'index.html', 'text/html') } }];
"""

REPORT_NODE = {
    "parameters": {"jsCode": REPORT_CODE, "mode": "runOnceForAllItems"},
    "id": "a1000000-0000-4000-8000-000000000005",
    "name": "Node 5",
    "type": "n8n-nodes-base.code",
    "typeVersion": 2,
    "position": [1180, 300],
    "notes": "CORRECTION 4: full HTML escaping (& < > \" ') because review "
             "text is untrusted input rendered into a PDF; the doc's esc() "
             "only escaped & and <. Also adds an explicit length-mismatch "
             "guard, a LOW-CONFIDENCE band for ambiguous reviews, and a single "
             "shared report date. Binary is named index.html - Gotenberg "
             "requires that exact filename.",
}

# ---------------------------------------------------------------------------
# Node 6 - HTTP Request -> Gotenberg (HTML -> PDF)
# ---------------------------------------------------------------------------
GOTENBERG_NODE = {
    "parameters": {
        "method": "POST",
        "url": "http://gotenberg:3000/forms/chromium/convert/html",
        "sendHeaders": True,
        "headerParameters": {
            "parameters": [
                {"name": "Gotenberg-Output-Filename",
                 "value": "review-pulso-report.pdf"}
            ]
        },
        "sendBody": True,
        # THE EXACT VALUE MATTERS, AND A WRONG ONE FAILS SILENTLY.
        # n8n's HTTP Request node defines this option as
        #   { name: 'Form-Data', value: 'multipart-form-data' }
        # and `bodyParameters` is only rendered when
        #   contentType: ['multipart-form-data']
        # This node previously used "form-data", which is not a valid enum
        # value at all. n8n did not error on it - it simply never displayed or
        # read bodyParameters, so the request went out with NO BODY and
        # Gotenberg received an empty multipart. That was the cause of a live
        # HTTP 500 "Error in workflow" that cost a round trip to diagnose.
        "contentType": "multipart-form-data",
        "bodyParameters": {
            "parameters": [
                # Gotenberg's multipart field is literally named "files".
                # The binary's own fileName (index.html) travels with it, which
                # is what satisfies Gotenberg's "must be index.html" rule.
                {"parameterType": "formBinaryData",
                 "name": "files",
                 "inputDataFieldName": "data"},
                # REQUIRED. The donut, the gauge and the header gradient are
                # CSS *backgrounds*. Chromium's print path drops backgrounds
                # unless printBackground=true, and the report comes out with
                # blank white charts.
                {"parameterType": "formData", "name": "printBackground", "value": "true"},
                # CORRECTION 2. Without this, Gotenberg's Chromium uses its
                # default LETTER paper and IGNORES the CSS `@page { size: A4 }`,
                # so the 210mm-wide .page div is rendered onto 8.5in-wide paper
                # and the layout mis-scales / clips at the right edge.
                {"parameterType": "formData", "name": "preferCssPageSize", "value": "true"},
                # Portrait A4 explicitly, as a belt-and-braces measure in case
                # preferCssPageSize is ever dropped.
                {"parameterType": "formData", "name": "paperWidth", "value": "8.27"},
                {"parameterType": "formData", "name": "paperHeight", "value": "11.69"},
                {"parameterType": "formData", "name": "marginTop", "value": "0"},
                {"parameterType": "formData", "name": "marginBottom", "value": "0"},
                {"parameterType": "formData", "name": "marginLeft", "value": "0"},
                {"parameterType": "formData", "name": "marginRight", "value": "0"},
            ]
        },
        "options": {
            "timeout": 60000,
            "response": {
                "response": {
                    "responseFormat": "file",
                    "outputPropertyName": "data",
                }
            },
        },
    },
    "id": "a1000000-0000-4000-8000-000000000006",
    "name": "Node 6",
    "type": "n8n-nodes-base.httpRequest",
    "typeVersion": 4.2,
    "position": [1420, 300],
    "retryOnFail": True,
    "maxTries": 3,
    "waitBetweenTries": 2000,
    "notes": "CORRECTION 2: preferCssPageSize=true is essential - without it "
             "Gotenberg defaults to LETTER and ignores the CSS @page A4 rule, "
             "clipping the 210mm page. printBackground=true is essential for "
             "the donut/gauge/gradient. Output binary field is 'data'.",
}
# ===========================================================================
#  DELIVERY REWORKED: Telegram -> a downloads folder on the operator's disk
# ===========================================================================
#
# WHAT CHANGED AND WHY
#
# The source doc's final step is Telegram (bot token, chat ID, BotFather
# setup, a credential in n8n). The operator chose a plain downloads folder on
# the host instead. That removes the entire Telegram dependency: no bot, no
# token, no chat ID, no credential, two fewer nodes, and nothing to go stale.
#
# Node 6 (Gotenberg) already returns the PDF as binary field `data`. A
# "Read/Write Files from Disk" node in `write` operation just puts it on disk.
# In n8n that node resolves RELATIVE paths against N8N_USER_FOLDER, which
# defaults to ~/.n8n-files, so the compose file mounts the host's download
# directory at /home/node/.n8n-files/downloads and we write to
# `downloads/<name>.pdf`.
#
# ---------------------------------------------------------------------------
# THE SECURITY ISSUE THIS INTRODUCES - read this before deploying
# ---------------------------------------------------------------------------
# With Telegram, the report went to a chat ID. A hostile uploader could burn
# some CPU and post noise to your channel. They could NOT choose where on your
# filesystem a file landed, because the filename was fixed by us.
#
# Writing to disk means the filename is derived from a field the CLIENT
# CONTROLS: `business_name`. That is a path-traversal vector. A business_name
# of `../../etc/cron.d/x` would escape the downloads directory entirely. And
# because the form uses a shared secret rather than per-client auth, "who
# uploaded" is not a meaningful audit trail either.
#
# So Node 7 does the following, and it is deliberately paranoid:
#   1. Slugifies business_name down to [a-z0-9-], which structurally cannot
#      contain '/', '.', or a null byte.
#   2. Then ASSERTS the finished filename against a strict allowlist regex.
#      Belt and braces: the slug makes traversal impossible, the assertion
#      means that if the slug function is ever changed, the workflow fails
#      loudly instead of writing somewhere unexpected.
#   3. Caps length so filenames cannot blow past filesystem limits.
#   4. Puts a UTC timestamp in the name so repeat uploads never collide and
#      never overwrite.
#   5. Strips accents, so "Café Korner" and "Cafe Korner" both work and do
#      not produce mojibake filenames.
#
# A second, subtler concern: a client can upload repeatedly and each run
# writes two files. With auth being a shared link, that is a disk-fill risk
# rather than an information-disclosure one. Worth knowing; not fixable in the
# workflow, needs a cron on the host. See JOURNAL.md.
# ---------------------------------------------------------------------------
FILENAME_CODE = r"""// Build a filesystem-safe output name from client-controlled input, and
// emit the PDF plus a JSON sidecar carrying the full statistics.
//
// WHY THIS NODE EXISTS
// The PDF now lands in a folder on the operator's disk instead of Telegram,
// so the filename is derived from `business_name` - a field the CLIENT
// CONTROLS. Without sanitising it, a value like `../../etc/cron.d/x` would be
// a path-traversal escape out of the downloads directory.
//
// Order matters: Node 5's stats are read from that node by name, and the
// binary from Node 6 (the PDF) is passed through untouched.

// ---- slugify ------------------------------------------------------------
// Reduce to [a-z0-9-]. After this function it is STRUCTURALLY IMPOSSIBLE for
// the value to contain a path separator, a dot-segment, or a null byte.
const slug = (raw) => {
  let s = String(raw ?? '')
    .normalize('NFKD')                      // split accented chars into base + mark
    .replace(/[\u0300-\u036f]/g, '')        // drop the combining marks
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')            // everything else becomes a dash
    .replace(/^-+|-+$/g, '');               // trim leading/trailing dashes
  s = s.slice(0, 40).replace(/-+$/, '');     // cap length, re-trim after capping
  return s || 'unnamed-business';
};

const stats  = $('Node 5').first().json;
const meta   = $('Node 3').first().json;
// CORRECTION: same as Node 3 - a Webhook nests the submitted fields under
// .body, a Form Trigger does not. Read whichever is present.
const rawForm = $('Node 1').first().json;
const form   = (rawForm && rawForm.body && typeof rawForm.body === 'object')
  ? rawForm.body : rawForm;

const business = slug(form.business_name || meta.business_name);

// UTC timestamp, compact and sortable. YYYYMMDD-HHMMSS.
// UTC rather than Asia/Manila so the name is unambiguous wherever it is
// opened, and so it cannot be ambiguous across a DST-style clock change.
const now = new Date();
const pad = (n) => String(n).padStart(2, '0');
const stamp = `${now.getUTCFullYear()}${pad(now.getUTCMonth() + 1)}`
            + `${pad(now.getUTCDate())}-${pad(now.getUTCHours())}`
            + `${pad(now.getUTCMinutes())}${pad(now.getUTCSeconds())}`;

const p = stats.stats.pct;
const base = `review-pulso__${business}__${stamp}`
           + `__n${stats.stats.total}`
           + `__pos${p.positive}__neu${p.neutral}__neg${p.negative}`
           + `__net${stats.stats.net_score}`;

// ASSERTION. The slug above already makes traversal impossible; this is the
// second lock. If someone later "simplifies" the slug function, this fails
// loudly rather than writing outside the downloads directory.
const SAFE = /^[A-Za-z0-9._-]{1,180}$/;
if (!SAFE.test(base) || base.includes('..')) {
  throw new Error(
    `Refusing to write: generated filename "${base}" failed the safety check. `
    + `This is a bug in the slug function, not in your data.`
  );
}

const pdfName    = `downloads/${base}.pdf`;
const manifestName = `downloads/${base}.json`;

// The sidecar carries what a human would otherwise have to open the PDF to
// learn: the exact counts, net score, verdict, ambiguity count and the full
// insight list. Deliberately does NOT include the review text, so the folder
// does not become a second copy of customer data.
const manifest = {
  business_name: form.business_name || meta.business_name,
  context: meta.context || '',
  generated_at_utc: now.toISOString(),
  total_reviews: stats.stats.total,
  counts: stats.stats.counts,
  percentages: p,
  net_score: stats.stats.net_score,
  verdict: stats.verdict,
  low_confidence_reviews: stats.stats.low_confidence,
  insights: stats.insights,
  pdf_filename: base + '.pdf',
  model: 'TagaSenti v4 (Apache-2.0, DOI 10.57967/hf/9620)',
};

// Pass the PDF binary through untouched, and add the manifest as a second
// binary field.
return [{
  json: {
    pdf_filename: base + '.pdf',
    manifest_filename: base + '.json',
    writes: [pdfName, manifestName],
    // The full stats object is carried through, not just three scalars.
    // The Respond to Webhook node at the end of the chain reads $json.stats,
    // and it previously did not exist here - so the final node threw and the
    // whole run failed with "Error in workflow" even though the PDF had
    // already been written. Carrying the object is what makes the two nodes
    // agree.
    stats: stats.stats,
    business_name: form.business_name || meta.business_name,
    total: stats.stats.total,
    net_score: stats.stats.net_score,
    verdict: stats.verdict,
  },
  // Pass the PDF (from Node 6) through to Node 8.
  //
  // THIS ONLY WORKS WITH n8n's DEFAULT in-memory binary mode. With
  // N8N_DEFAULT_BINARY_DATA_MODE=filesystem, a binary is not inline base64
  // but an id into n8n's binary store, and passing such a reference through a
  // Code node's returned binary map does not reliably survive - the node
  // drops it and Node 8 fails with "The item has no binary field 'data'".
  //
  // The filesystem mode was removed from docker-compose.yml for this reason.
  // Do not re-add it without re-testing Node 7 -> Node 8. The payloads are a
  // ~10KB CSV and a ~120KB PDF, so in-memory costs nothing here.
  binary: {
    data: items[0].binary.data,                     // the PDF from Node 6
  },
}];"""

NAME_NODE = {
    "parameters": {"jsCode": FILENAME_CODE, "mode": "runOnceForAllItems"},
    "id": "a1000000-0000-4000-8000-000000000007",
    "name": "Node 7 - Filename",
    "type": "n8n-nodes-base.code",
    "typeVersion": 2,
    "position": [1660, 300],
    "notes": "SECURITY: builds the output filename from client-controlled "
             "business_name. Slugified to [a-z0-9-] (so '/' and '..' cannot "
             "survive) AND then asserted against a strict allowlist regex. "
             "If you edit the slug function, keep the assertion. Also passes "
             "the PDF binary through and adds a JSON sidecar.",
}

# --- Node 8 / 9: the actual disk writes ----------------------------------
# The n8n "Read/Write Files from Disk" node, operation "write". Relative
# paths resolve against N8N_USER_FOLDER, which defaults to ~/.n8n-files, where
# compose mounts the host's download directory as downloads/.
#
# These paths MUST NOT begin with a slash - an absolute path is how you would
# accidentally write outside the intended directory.
WRITE_PDF_NODE = {
    "parameters": {
        "operation": "write",
        # ABSOLUTE path, on purpose.
        #
        # n8n 2.0 introduced a breaking change: the Read/Write Files from Disk
        # node is sandboxed to ~/.n8n-files and refuses anything else with
        # "Access to the file is not allowed. Allowed paths:
        # /home/node/.n8n-files". A RELATIVE path is resolved by n8n, and
        # whether it resolves inside that sandbox is a detail of n8n's
        # internals rather than something the workflow states. Writing the
        # absolute path makes the guarantee explicit: it is inside the allowed
        # directory by construction, and the compose mount is what makes it
        # land on the host.
        #
        # The directory component is a LITERAL here. Only the basename comes
        # from Node 7, and Node 7 asserts that basename against a strict
        # allowlist regex, so client input cannot influence where anything is
        # written.
        "fileName": "=/home/node/.n8n-files/downloads/{{ $('Node 7 - Filename').first().json.pdf_filename }}",
        "inputDataFieldName": "data",
        "options": {},
    },
    "id": "a1000000-0000-4000-8000-000000000008",
    "name": "Node 8 - Save PDF",
    "type": "n8n-nodes-base.readWriteFile",
    "typeVersion": 1,
    "position": [1900, 220],
    "notes": "Writes the PDF into the host downloads folder. NOTE: the path "
             "resolution here (relative to N8N_USER_FOLDER) is the one part "
             "of this delivery path NOT verified by execution, because it "
             "needs a running n8n. Verify the file appears, and check the "
             "n8n log for the resolved absolute path, before trusting it.",
}

# ---------------------------------------------------------------------------
# THE JSON SIDECAR WAS REMOVED
# ---------------------------------------------------------------------------
# A final Node wrote a JSON summary alongside the PDF. It has been deleted,
# and the reason is worth recording because it is a judgement, not a defect.
#
# The sidecar cost FOUR debugging cycles and never worked once:
#   1. It was first written as a byte-identical copy of the PDF, because two
#      binary fields were carried on one item (§10.0.15).
#   2. Splitting the two writes apart introduced the filesystem-mode
#      pass-through failure (§10.0.16).
#   3. Making the write nodes best-effort stopped it failing the run, but it
#      still produced nothing (§10.0.18).
#   4. It was then also emptying the HTTP response body, because a
#      best-effort node that errors hands the next node an empty item.
#
# What it would have added, measured against what already exists:
#
#   the sidecar had   ->  the filename already has
#     business name     -> business (slugged)
#     timestamp         -> UTC stamp
#     review count      -> n
#     per-class %       -> pos / neu / neg
#     net score         -> net
#     the insight list  -> the PDF renders the insight list
#     low-confidence #  -> (not in the filename)
#
# So the ONLY field the sidecar uniquely held was the low-confidence count,
# and it is a derived number the PDF already displays. Everything else was
# duplicated.
#
# The filename is a better interface anyway: sorting the folder by name sorts
# by date, and `ls` shows the verdict statistics without opening anything. A
# sidecar is one more file per run to keep, move, and delete.
#
# The trade-off, stated honestly: if a client ever needs the raw insight TEXT
# programmatically, this has to come back - but it should be built from the
# same Code node that builds the report, and it should not be on the critical
# path to delivering the PDF.

NODES = [
    WEBHOOK_NODE, EXTRACT_NODE, CLEAN_NODE, IF_VALID_NODE, RESPOND_ERROR_NODE,
    HTTP_TAGASENTI_NODE, REPORT_NODE, GOTENBERG_NODE, NAME_NODE, WRITE_PDF_NODE,
    RESPOND_NODE,
]

# Straight line, with one branch: Node 3 now RETURNS a rejection rather than
# throwing, so Node 3b routes it to Node 3c (HTTP 400) instead of the execution
# dying and n8n substituting {"message":"Error in workflow"}.
#
# Node 3b is an IF node and therefore has TWO outputs:
#   output 0 = "true"  = pipeline_error is present = bad input  -> Node 3c
#   output 1 = "false" = no error                     = proceed  -> Node 4
CONNECTIONS = {
    "Node 1": {"main": [[{"node": "Node 2", "type": "main", "index": 0}]]},
    "Node 2": {"main": [[{"node": "Node 3", "type": "main", "index": 0}]]},
    "Node 3": {"main": [[{"node": "Node 3b - Input valid?",
                          "type": "main", "index": 0}]]},
    # index 0 is the true branch (rejection), index 1 the false branch (happy path)
    "Node 3b - Input valid?": {"main": [
        [{"node": "Node 3c - Respond bad input", "type": "main", "index": 0}],
        [{"node": "Node 4", "type": "main", "index": 0}],
    ]},
    "Node 4": {"main": [[{"node": "Node 5", "type": "main", "index": 0}]]},
    "Node 5": {"main": [[{"node": "Node 6", "type": "main", "index": 0}]]},
    "Node 6": {"main": [[{"node": "Node 7 - Filename",
                          "type": "main", "index": 0}]]},
    "Node 7 - Filename": {"main": [[
        {"node": "Node 8 - Save PDF", "type": "main", "index": 0}]]},
    "Node 8 - Save PDF": {"main": [[
        {"node": "Node 1b - Respond", "type": "main", "index": 0}]]},
}

WORKFLOW = {
    "name": "Review Pulso - Filipino Sentiment Report (v1.3, disk delivery)",
    "nodes": NODES,
    "connections": CONNECTIONS,
    "settings": {
        "executionOrder": "v1",
        # CORRECTION 3: raised from the doc's 300s to 900s.
        # Node 4 may legitimately take up to 600s (1000 reviews at the
        # 128-token worst case measured at 360s, with headroom), and Node 6
        # adds up to 60s of Chromium rendering. A 300s ceiling would abort
        # healthy runs and report a misleading "workflow timed out" instead
        # of a clean HTTP error from Node 4.
        "executionTimeout": 900000,
    },
    "staticData": None,
    "pinData": {},
    "active": False,
    "versionId": "1.3.0",
    "meta": {"instanceId": "review-pulso-local"},
    "tags": [],
}

if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    # Output goes to the repo-root `workflow/`, alongside the importable JSON.
    out = os.path.join(os.path.dirname(here), "workflow",
                       "review-pulso.workflow.json")
    with open(out, "w") as fh:
        json.dump(WORKFLOW, fh, indent=2, ensure_ascii=False)

    size = os.path.getsize(out)
    print(f"wrote {out}")
    print(f"  {len(NODES)} nodes, {size:,} bytes")
    print()
    print("Node graph:")
    for n in NODES:
        print(f"  {n['name']:<26} {n['type'].split('.')[-1]}")
    print()
    print("Delivery: PDF + JSON sidecar -> the host downloads folder.")
    print("  (Telegram was removed at the operator's request.)")
    print()
    print("Entry point: a self-hosted drag-and-drop page (upload/index.html).")
    print("  (n8n's Form Trigger was removed: its test form cannot accept file")
    print("   uploads and its production form never registered on this")
    print("   instance. See the Node 1 comment.)")
    print()
    print("UPLOAD PAGE for clients (real drag-and-drop):")
    print("  http://localhost:8080/")
    print()
    print("WEBHOOK the page posts to:")
    print(f"  http://localhost:5678/webhook/{WEBHOOK_PATH}")
    print()
    print("IMPORT: n8n UI -> Workflows -> three-dot menu -> Import from File")
    print("THEN:  click PUBLISH in the top-right corner of the editor.")
    print("       n8n 2.x has no Active toggle - Publish is what registers the")
    print("       production webhook. No credential to attach.")
