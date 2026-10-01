#!/usr/bin/env python3
"""
STEP 7 - Render the Node 5 HTML to a real file and (optionally) to PDF.

WHY
  The Code node test (06) asserts that the right STRINGS are in the HTML. That
  is not the same as the report looking right. CSS bugs - a donut whose hole
  is the wrong size, a gauge marker off the end of its track, a card grid that
  overflows the page - are invisible to string assertions. They only show up
  when a browser lays the document out.

  So this step produces two artefacts:
    sample-report.html   - open in any browser to inspect the design
    sample-report.pdf    - the actual Gotenberg output, IF Gotenberg is up

  The PDF path is the only thing in this build that genuinely cannot be
  verified without Docker running. Everything else is verified already.

USAGE
    python3 07_render_sample_report.py
    python3 07_render_sample_report.py --live-pdf   # needs Gotenberg up
"""

import base64
import json
import os
import subprocess
import sys
import tempfile

JOURNAL_DIR = os.path.dirname(os.path.abspath(__file__))
# `product/` and `upload/` were promoted to the repo root during the
# restructure; agent_journal keeps only the build record.
REPO_ROOT = os.path.dirname(JOURNAL_DIR)
WORKFLOW_JSON = os.path.join(REPO_ROOT, "workflow", "review-pulso.workflow.json")
OUT_HTML = os.path.join(JOURNAL_DIR, "evidence", "sample-report.html")
OUT_PDF = os.path.join(JOURNAL_DIR, "evidence", "sample-report.pdf")

DOC_TEST_CSV = [
    "Ang bilis ng delivery at ang sarap ng food!",
    "Medyo matagal ang paghihintay at malamig na ang ulam.",
    "Okay lang naman, hindi maganda hindi rin pangit.",
    "Grabe ang panget ng packaging, basag na basag.",
    "Sulit na sulit, babalik ulit ako dito pramis",
]

# Same stub predictions as the Code node test, so the rendered report and the
# assertions describe the same document.
STUB_RESULTS = [
    ("Positive", {"Negative": 0.021, "Neutral": 0.025, "Positive": 0.954}),
    ("Negative", {"Negative": 0.812, "Neutral": 0.151, "Positive": 0.037}),
    ("Neutral",  {"Negative": 0.401, "Neutral": 0.455, "Positive": 0.144}),
    ("Negative", {"Negative": 0.934, "Neutral": 0.048, "Positive": 0.018}),
    ("Positive", {"Negative": 0.011, "Neutral": 0.033, "Positive": 0.956}),
]

RENDER_JS = r"""
const NODE5 = __NODE5__;
const NODE3 = __NODE3__;
const DOC_REVIEWS = __REVIEWS__;
const STUB = __STUB__;

function $(name) {
  const named = {
    'Node 1': { business_name: 'SariSari Store Malate', context: 'Q3 2026' },
  };
  return { first: () => ({ json: named[name] || {} }) };
}

(async () => {
  // Run Node 3 for real so the Node 3 -> Node 5 handoff is exercised.
  const fn3 = new Function('items', '$', '$input', 'Buffer',
    'return (async () => { ' + NODE3 + ' })();');
  const items = DOC_REVIEWS.map(t => ({ json: { review: t } }));
  const $input3 = { first: () => items[0] };
  const n3out = (await fn3(items, $, $input3, Buffer))[0].json;

  const named = { 'Node 3': n3out };
  const $5 = (name) => ({ first: () => ({ json: named[name] || {} }) });
  const body = NODE5.replace(/this\.helpers/g, '__helpers');
  const fn5 = new Function('__helpers', 'items', '$', '$input', 'Buffer',
    'return (async () => { ' + body + ' })();');
  const helpers = { prepareBinaryData: async (b) => b.toString('base64') };
  const item = { json: { results: STUB.map(([label, scores]) => ({ label, scores })) } };
  const $input5 = { first: () => item };
  const r = await fn5(helpers, [item], $5, $input5, Buffer);

  process.stdout.write(r[0].binary.data);
})();
"""


def main():
    live_pdf = "--live-pdf" in sys.argv
    wf = json.load(open(WORKFLOW_JSON))
    codes = {n["name"]: n["parameters"]["jsCode"]
             for n in wf["nodes"] if "jsCode" in n.get("parameters", {})}

    js = (RENDER_JS
          .replace("__NODE5__", json.dumps(codes["Node 5"]))
          .replace("__NODE3__", json.dumps(codes["Node 3"]))
          .replace("__REVIEWS__", json.dumps(DOC_TEST_CSV))
          .replace("__STUB__", json.dumps(STUB_RESULTS)))

    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False) as fh:
        fh.write(js)
        path = fh.name
    try:
        proc = subprocess.run(["node", path], capture_output=True, text=True)
    finally:
        os.unlink(path)

    if proc.returncode != 0:
        print("Node 5 failed to render:\n", proc.stderr[-3000:])
        return 1

    html = base64.b64decode(proc.stdout).decode("utf-8")
    with open(OUT_HTML, "w") as fh:
        fh.write(html)
    print(f"wrote {OUT_HTML}  ({len(html):,} bytes)")
    print("  open it in a browser to check the design before wiring n8n.")

    if not live_pdf:
        print("\n(--live-pdf not passed, so no PDF was produced)")
        print("  Once Gotenberg is up, re-run with --live-pdf to confirm the")
        print("  A4 layout and printBackground behave as the CSS expects.")
        return 0

    # Render via Gotenberg using EXACTLY the form fields Node 6 sends. If this
    # produces a clipped Letter-sized page, the n8n node will too.
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as fh:
        fh.write(html)
        html_path = fh.name
    try:
        url = os.environ.get("GOTENBERG_URL", "http://localhost:3000")
        cmd = [
            "curl", "-sS", "-X", "POST", f"{url}/forms/chromium/convert/html",
            "-H", "Gotenberg-Output-Filename: review-pulso-report.pdf",
            "--form", f"files=@{html_path}",
            "--form", "printBackground=true",
            "--form", "preferCssPageSize=true",
            "--form", "paperWidth=8.27",
            "--form", "paperHeight=11.69",
            "--form", "marginTop=0",
            "--form", "marginBottom=0",
            "--form", "marginLeft=0",
            "--form", "marginRight=0",
            "-o", OUT_PDF,
        ]
        print(f"\nrendering PDF via {url} ...")
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            print("curl failed:", proc.stderr[-2000:])
            print("Is Gotenberg running?  docker compose ps gotenberg")
            return 1
        size = os.path.getsize(OUT_PDF)
        if size < 1000:
            print(f"Gotenberg returned {size} bytes - that is an error page:")
            print(open(OUT_PDF, errors="replace").read()[:2000])
            return 1
        print(f"wrote {OUT_PDF}  ({size:,} bytes)")
        print("  CHECK: page is A4 portrait (210x297mm), the donut shows three")
        print("  colours, and the gauge marker is inside its track. If the")
        print("  charts are white, printBackground is not being applied.")
        return 0
    finally:
        os.unlink(html_path)


if __name__ == "__main__":
    sys.exit(main())
