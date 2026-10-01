#!/usr/bin/env python3
"""
STEP 13 - Fit the report onto ONE A4 page, measured rather than guessed.

WHY THIS EXISTS
The spec (and the source doc) says "polished one-page PDF report". The smoke
test's Gotenberg output was TWO pages, with the overflow being exactly the
"Recommended actions" list plus the footer - roughly 35mm too much content.

The first fix (card cap 5 -> 3) did not help, and the reason is instructive:
the 5-row test CSV contains only 2 positive and 2 negative reviews, so a cap
of 3 and a cap of 5 render IDENTICALLY for that input. The page was overfull
for reasons unrelated to the number of cards. Measuring was necessary.

WHY IT RUNS LOCALLY INSTEAD OF VIA GOTENBERG
Gotenberg is the authority on the final output, but it needs Docker, which the
agent's session cannot reach. This box has google-chrome, which uses the same
Skia PDF pipeline Gotenberg's Chromium does, so a render/measure loop here
takes seconds instead of a round trip to the operator.

The local result is treated as a fast proxy. Gotenberg remains the
confirmation, via 09_smoke_test.sh.

USAGE
    python3 13_fit_one_page.py            # render + report page count
    python3 13_fit_one_page.py --stress   # also test the worst case (6 cards)
"""

import os
import re
import subprocess
import sys
import tempfile

JOURNAL_DIR = os.path.dirname(os.path.abspath(__file__))
# `product/` and `upload/` were promoted to the repo root during the
# restructure; agent_journal keeps only the build record.
REPO_ROOT = os.path.dirname(JOURNAL_DIR)
CHROME = "/usr/bin/google-chrome"
HTML = os.path.join(JOURNAL_DIR, "evidence", "sample-report.html")

# A4 at 96 CSS px/inch: 210mm x 297mm -> 793.7 x 1122.5 px
A4_PX = (794, 1123)


def render_pdf(html_path, out_path):
    """Print the HTML to PDF with no margins, mirroring the CSS @page rule."""
    cmd = [
        CHROME, "--headless", "--disable-gpu", "--no-sandbox",
        "--no-pdf-header-footer",
        f"--print-to-pdf={out_path}",
        # Match the report's own A4/margin:0 declaration rather than Chrome's
        # default Letter, so the measurement reflects the real layout.
        f"--print-to-pdf-no-header",
        f"file://{html_path}",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if not os.path.exists(out_path):
        print("chrome failed:", proc.stderr[-1500:])
        return None
    return out_path


def page_count(pdf_path):
    data = open(pdf_path, "rb").read()
    counts = re.findall(rb"/Type\s*/Page[^s]", data)
    return len(counts)


def media_box(pdf_path):
    data = open(pdf_path, "rb").read()
    m = re.search(rb"MediaBox\s*\[([^\]]*)\]", data)
    if not m:
        return None
    parts = m.group(1).split()
    return float(parts[2]), float(parts[3])


def check(label, html_path):
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "out.pdf")
        if render_pdf(html_path, out) is None:
            return None
        pages = page_count(out)
        box = media_box(out)
        w_mm = box[0] / 72 * 25.4 if box else 0
        h_mm = box[1] / 72 * 25.4 if box else 0
        a4 = abs(w_mm - 210) < 3 and abs(h_mm - 297) < 3
        status = "OK " if pages == 1 else "OVER"
        print(f"  [{status}] {label:<28} pages={pages}  "
              f"{w_mm:.0f}x{h_mm:.0f}mm  a4={a4}")
        return pages


def main():
    if not os.path.exists(CHROME):
        sys.exit(f"FATAL: {CHROME} not found")
    if not os.path.exists(HTML):
        sys.exit(f"FATAL: {HTML} missing - run 07_render_sample_report.py")

    print("=" * 78)
    print("One-page fit check (local Chrome, proxy for Gotenberg)")
    print("=" * 78)

    pages = check("5-row test CSV (from the doc)", HTML)
    if pages is None:
        return 1

    if pages != 1:
        print()
        print("  STILL OVERFULL. The overflow is the recommendations + footer.")
        print("  Reclaim vertical space in the CSS in 05_generate_workflow_json.py.")
    else:
        print()
        print("  ONE PAGE. Confirmed locally; Gotenberg must still agree.")

    if "--stress" in sys.argv:
        print()
        print("  Stress case: 6 negatives + 3 positives, so the 3-card cap is")
        print("  exercised on BOTH sides (this is what the 5-row CSV misses):")
        stress = make_stress_html()
        sp = check("worst case (6 neg, 3 pos)", stress)
        if sp and sp != 1:
            print("    -> the 5-row CSV is not a valid one-page test. Always")
            print("       check the worst case too.")

    print("=" * 78)
    return 0 if pages == 1 else 2


def make_stress_html():
    """
    Build a worst-case HTML by re-running Node 5 with 9 reviews (6 neg, 3 pos).

    Reuses the real generator output so the measurement reflects the real
    code, not a hand-edited approximation.
    """
    import json
    wf = json.load(open(os.path.join(REPO_ROOT, "workflow", "review-pulso.workflow.json")))
    code = wf["nodes"][0]  # placeholder, replaced below
    code5 = [n for n in wf["nodes"] if n["name"] == "Node 5"][0]["parameters"]["jsCode"]
    code3 = [n for n in wf["nodes"] if n["name"] == "Node 3"][0]["parameters"]["jsCode"]

    samples = [
        ("Pangit ng packaging, bumagsak agad pagdating. Nakita namin agad na basag na basag, grabe ang pagkakainconsistent ng tindera.", "Negative", 0.99),
        ("Grabe ang paghihintay, dalawang oras bago ma serving. Naiyak na ako sa paghihintay, ang galit ko sobrang tao na.", "Negative", 0.97),
        ("Hindi worth it ang bayad para sa ganitong kalidad. Napakasamang experience, hindi na ako babalik sa inyo.", "Negative", 0.95),
        ("May daga sa korte at amoy sa kusina. Nakakatigil sa amoy, ang pagkain sana naman ay hindi galing sa inyo.", "Negative", 0.93),
        ("Mabilis naman ang delivery pero mali ang address. Hindi nila sinunod ang aking request, sobrang nakababahala ako.", "Negative", 0.91),
        ("Okay lang, hindi masyado maganda pero hindi rin pangit. Average lang ang experience ko dito sa inyo.", "Negative", 0.55),
        ("Sobrang ganda ng tela at worth it ang price! Babalik naman ako, maraming salamat sa mga staff.", "Positive", 0.96),
        ("Mabilis ang paghahaini at napaka friendly ng crew. Salamat sa mainit na pagtanggap, highly recommended!", "Positive", 0.94),
        ("Okay lang naman po, sakto lang. Walang complains ko, basta lang average ang service.", "Positive", 0.80),
    ]

    # The stub probabilities are built here in Python rather than in JS, so
    # the arithmetic is easy to read and verify by eye.
    def mkstub(label, conf):
        rival = round((1 - conf) / 2, 4)
        d = {"Negative": rival, "Neutral": rival, "Positive": rival}
        d[label] = conf
        return [label, d]

    st = {
        "const REVIEWS": json.dumps([s[0] for s in samples]),
        "const STUB": json.dumps([mkstub(s[1], s[2]) for s in samples]),
        "const NODE5": json.dumps(code5),
        "const NODE3": json.dumps(code3),
    }
    js = (
        "const NODE5 = " + st["const NODE5"] + ";\n"
        "const NODE3 = " + st["const NODE3"] + ";\n"
        "const REVIEWS = " + st["const REVIEWS"] + ";\n"
        "const STUB = " + st["const STUB"] + ";\n"
        + r"""
const $ = (n) => ({ first: () => ({ json: n === 'Node 3'
    ? { reviews: REVIEWS, business_name: 'Stress Test Cafe', context: 'worst case' }
    : { business_name: 'Stress Test Cafe', context: 'worst case' } }) });
(async () => {
  const fn5 = new Function('__h','items','$','$input','Buffer',
    'return (async () => { ' + NODE5.replace(/this\.helpers/g,'__h') + ' })();');
  const item = { json: { results: STUB.map(([label,scores]) => ({label,scores})) } };
  const r = await fn5({ prepareBinaryData: async b => b.toString('base64') },
    [item], $, { first: () => item }, Buffer);
  process.stdout.write(Buffer.from(r[0].binary.data, 'base64').toString('utf-8'));
})();
"""
    )
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False) as fh:
        fh.write(js)
        p = fh.name
    try:
        proc = subprocess.run(["node", p], capture_output=True, text=True, timeout=120)
    finally:
        os.unlink(p)
    if proc.returncode != 0:
        print("stress render failed:", proc.stderr[-800:])
        return HTML
    out = os.path.join(tempfile.gettempdir(), "stress-report.html")
    with open(out, "w") as fh:
        fh.write(proc.stdout)
    return out


if __name__ == "__main__":
    sys.exit(main())
