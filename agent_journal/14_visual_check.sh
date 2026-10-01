#!/usr/bin/env bash
# ===========================================================================
#  STEP 14 - Screenshot the report so it can be LOOKED AT.
#
#  WHY THIS EXISTS
#  Test 06 (06_test_code_nodes.py) asserts that the right STRINGS are in the
#  HTML. It passed 42/42 while the rendered report contained:
#
#    "1. 1. Address the leading complaint directly - ..."   <- double numbering
#    and the donut's "NET SCORE" caption overflowing onto the coloured ring
#
#  Neither is detectable by asserting on substrings. The first is an HTML
#  semantics bug (an <ol> numbers its own <li>), the second is a pure geometry
#  bug. Both were found by rendering the page and looking at it.
#
#  This script therefore produces a PNG that a human (or an agent with image
#  input) can actually inspect. It is the only check in this project that
#  covers visual defects.
#
#  It uses the local google-chrome rather than Gotenberg, for the same reason
#  13_fit_one_page.py does: the agent's session cannot reach Docker, and a
#  screenshot needs no Docker at all. Gotenberg remains the authority on the
#  final PDF.
#
#  USAGE
#     bash 14_visual_check.sh
#  OUTPUT
#     14_visual_check.png   (top ~700px, for the header/KPIs/charts)
#     14_visual_check_full.png  (whole page)
#
#  WHAT TO LOOK FOR
#     - donut centre label fits INSIDE the white hole, not on the ring
#     - donut shows three distinct colours, proportions matching the KPIs
#     - gauge marker sits within the track, positioned for the net score
#     - header gradient and KPI top borders render (printBackground in Node 6)
#     - recommendations are numbered ONCE ("1." not "1. 1.")
#     - nothing is clipped at the right edge
#     - overall it is ONE page
# ===========================================================================
set -uo pipefail

JOURNAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHROME="/usr/bin/google-chrome"
HTML="$JOURNAL_DIR/evidence/sample-report.html"

if [ ! -f "$HTML" ]; then
    echo "FATAL: $HTML missing."
    echo "       run: python3 07_render_sample_report.py"
    exit 1
fi
if [ ! -x "$CHROME" ]; then
    echo "FATAL: $CHROME not found (needed to render a screenshot)"
    exit 1
fi

# A4 at 96 CSS px/inch.
W=794
H=1123

echo "=============================================================================="
echo "Rendering the report for visual inspection"
echo "=============================================================================="

"$CHROME" --headless --disable-gpu --no-sandbox --hide-scrollbars \
    --window-size=$W,$H \
    --screenshot="$JOURNAL_DIR/14_visual_check_full.png" \
    "file://$HTML" 2>/dev/null

FULL="$JOURNAL_DIR/14_visual_check_full.png"
if [ ! -s "$FULL" ]; then
    echo "FATAL: chrome produced no screenshot"
    exit 1
fi

# A second, top-of-page crop is more legible in a chat window than the full
# A4 page scaled down.
"$CHROME" --headless --disable-gpu --no-sandbox --hide-scrollbars \
    --window-size=$W,700 \
    --screenshot="$JOURNAL_DIR/14_visual_check.png" \
    "file://$HTML" 2>/dev/null

echo
echo "Wrote:"
ls -lh "$JOURNAL_DIR"/14_visual_check*.png | awk '{print "  " $5 "\t" $9}'
echo
echo "Now OPEN the images and check:"
echo "  1. donut centre label sits inside the white hole"
echo "  2. three distinct colours in the donut, matching the KPI percentages"
echo "  3. gauge marker within the track"
echo "  4. recommendations numbered once ('1.' not '1. 1.')"
echo "  5. nothing clipped at the right edge"
echo "  6. fits on ONE page"
echo
echo "Page count is verified mechanically by 13_fit_one_page.py; the items"
echo "above can only be checked by eye."
echo "=============================================================================="
