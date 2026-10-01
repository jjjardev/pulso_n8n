#!/usr/bin/env bash
# ===========================================================================
#  run_all_tests.sh — run every check for this project, in one command.
#
#  WHY THIS EXISTS
#  The checks existed as eight separate commands scattered across two READMEs,
#  which meant the most likely outcome was that nobody ran them. This runs
#  them all, reports pass/fail per suite with timings, and exits non-zero if
#  anything fails, so it is usable as a pre-deploy gate.
#
#  WHAT IS AND IS NOT COVERED
#  These tests cover the *code* and the *wiring*. They cannot tell you whether
#  the AI model is accurate for your business's reviews - that is the
#  validation gate in ../OPERATIONS.md section 13, and it needs human-labelled
#  business data. A fully green run here does NOT mean the product is ready to
#  sell.
#
#  USAGE
#      bash run_all_tests.sh              # the fast suites (default, ~30s)
#      bash run_all_tests.sh --full       # + the slow model-loading ones (~6 min)
#      bash run_all_tests.sh --list       # show what would run, run nothing
#      bash run_all_tests.sh --only 19 06 # run just these, by number
#      bash run_all_tests.sh --full --force  # skip the memory preflight
#
#  EXIT CODES
#      0  everything passed
#      1  at least one suite failed
#      2  a prerequisite is missing, or not enough free memory
#
#  ⚠️  MEMORY WARNING -- read this before using --full
#  The slow suites (02 and 16) each load the 537 MB TagaSenti model into RAM
#  on the HOST. The tagasenti container already holds its own copy, and
#  onnxruntime expands that considerably: measured RSS for the container's
#  uvicorn process alone is ~1.2 GB.
#
#  On this machine (7.6 GB total) running --full with the containers up
#  pushed the system into swap and the OOM killer terminated a process. That
#  actually happened during this build. So the runner now checks free memory
#  first and refuses to start the slow suites unless there is room, rather
#  than helping itself to a kernel panic.
#
#  If it refuses, you have three options:
#      1. run the 8 fast suites instead          -> bash run_all_tests.sh
#      2. free memory first:
#             sudo docker compose stop tagasenti
#         (you will not be able to score reports while it is stopped)
#      3. override the check if you know better -> bash run_all_tests.sh --full --force
# ===========================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE" || exit 2

# Parse the flags in any order, rather than assuming a position. An earlier
# version only understood --only as the SECOND argument, so the documented
# `bash run_all_tests.sh --only 19` printed "Unknown option: --only" and
# exited 2. Flags are now collected by name and the first bare word (if any)
# is the mode.
# ---------------------------------------------------------------------------
# Argument parsing
#
# Flags are collected by name in any order, and --only takes the value that
# FOLLOWS it. An earlier version only understood --only as the second
# argument, so the documented `run_all_tests.sh --only 19` reported
# "Unknown option: --only" and exited 2 - a documented feature that did not
# work. The modes are therefore kept separate from the selectors, rather than
# overloading one variable for both.
# ---------------------------------------------------------------------------
MODE="quick"          # quick | --full | --list
ONLY=""               # space-separated suite numbers, or empty
FORCE=0              # --force skips the memory preflight
POSITIONAL=()

args=("$@")
i=0
while [ $i -lt ${#args[@]} ]; do
    case "${args[$i]}" in
        --full)  MODE="--full" ;;
        --list)  MODE="--list" ;;
        --quick) MODE="quick" ;;
        --force) FORCE=1 ;;
        --only)
            i=$((i + 1))
            # Consume every following non-flag argument as a suite number.
            while [ $i -lt ${#args[@]} ]; do
                case "${args[$i]}" in
                    -*) break ;;
                    *)  ONLY="$ONLY ${args[$i]}"; i=$((i + 1)) ;;
                esac
            done
            i=$((i - 1))
            ;;
        -*)  echo "Unknown option: ${args[$i]}  (try --list)"; exit 2 ;;
        *)   POSITIONAL+=("${args[$i]}") ;;
    esac
    i=$((i + 1))
done

# A bare word is also accepted as the mode, so `run_all_tests.sh full` works.
if [ "${#POSITIONAL[@]}" -gt 0 ]; then
    case "${POSITIONAL[0]}" in
        full)  MODE="--full" ;;
        list)  MODE="--list" ;;
        quick) MODE="quick" ;;
        *)     echo "Unknown mode: ${POSITIONAL[0]}  (try --list)"; exit 2 ;;
    esac
fi

for tool in python3 node; do
    command -v "$tool" >/dev/null 2>&1 || {
        echo "FATAL: $tool not found on PATH."
        [ "$tool" = "node" ] && echo "  Needed for 10 and 12, and by n8n's Code node."
        [ "$tool" = "python3" ] && echo "  Needed for most suites."
        exit 2
    }
done

# ---- the suites ------------------------------------------------------------
# id | command | needs | what it protects
# "needs" is informational only; nothing here is skipped automatically, because
# a silently skipped test is worse than a failing one.
declare -a SUITES=(
  "06|python3 06_test_code_nodes.py|Check Node 3 and Node 5 (CSV cleaning, stats, report HTML, XSS escaping, filename cap)"
  "10|node 10_test_csv_parser.js|Check the fallback CSV parser against quoted commas, newlines, BOMs, CRLF"
  "12|node 12_test_filename_logic.js|Check 17 path-traversal attacks on the output filename"
  "13|python3 13_fit_one_page.py|Check the report still fits on one A4 page"
  "17|python3 17_test_upload_page.py|Check the upload page's script parses and its multipart contract matches n8n"
  "18|python3 18_test_proxy_and_cors.py|Reproduce the browser's CORS sequence through the nginx proxy"
  "19|python3 19_validate_node_params.py|Check every n8n node parameter against n8n's real enums, plus the data contracts between nodes"
  "20|bash 20_check_downloads_permissions.sh|Check the container user can actually write the reports folder"
)

# These import tagasenti/main.py directly, and that module imports onnxruntime
# and transformers. Those live in the CONTAINER image, not on a host by default,
# so on a fresh clone --full fails with:
#     ModuleNotFoundError: No module named 'onnxruntime'
# That is a missing prerequisite rather than a defect, and the traceback names
# nothing useful. README "Requires" has the pip install line.
declare -a SLOW=(
  "02|python3 02_test_tagasenti_service.py|Check main.py: batch ordering, the review cap, guard rails, and real throughput (slow: loads a 537MB model)"
  "16|python3 16_score_predictions.py|Measure the model's accuracy against data/test-100-ground-truth.csv (slow: loads the model)"
)

# ---- modes ----------------------------------------------------------------
case "$MODE" in
  --list)
    echo "Suites that run by default (bash run_all_tests.sh):"
    for row in "${SUITES[@]}"; do
        IFS='|' read -r id cmd needs <<< "$row"
        printf '  %-4s %s\n        %s\n' "$id" "$cmd" "$needs"
    done
    echo
    echo "Suites that need --full (they load the model and take minutes):"
    for row in "${SLOW[@]}"; do
        IFS='|' read -r id cmd needs <<< "$row"
        printf '  %-4s %s\n        %s\n' "$id" "$cmd" "$needs"
    done
    echo
    echo "Not run by either, because they need the containers:"
    echo "  09   bash 09_smoke_test.sh              End-to-end container health check"
    echo "  14   bash 14_visual_check.sh            Screenshot the report for visual inspection"
    exit 0
    ;;
  --full) ;;
  quick|"") ;;
  *) echo "Internal error: unhandled mode '$MODE'"; exit 2 ;;
esac

# ---- memory preflight for the slow suites --------------------------------
# The slow suites load a second and third copy of the 537 MB model into host
# RAM, on top of the ~1.2 GB the tagasenti container already holds. On a
# 7.6 GB machine that was enough to trigger the OOM killer, so the check is
# done BEFORE anything is loaded.
NEED_MB="${NEED_MB:-1800}"
if [ "$MODE" = "--full" ] && [ "$FORCE" -eq 0 ]; then
    avail_kb=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo 2>/dev/null)
    avail_mb=$(( ${avail_kb:-0} / 1024 ))
    if [ "$avail_mb" -lt "$NEED_MB" ]; then
        echo "=============================================================================="
        echo "REFUSING TO START THE SLOW SUITES - NOT ENOUGH MEMORY"
        echo "=============================================================================="
        echo "  available : ${avail_mb} MB"
        echo "  needed    : ${NEED_MB} MB (rough, for one model + workspace)"
        echo "  total     : $(awk '/^MemTotal:/ {printf "%d MB", $2/1024}' /proc/meminfo)"
        echo
        echo "  Suites 02 and 16 each load the 537 MB model into host RAM, and the"
        echo "  tagasenti container already holds its own copy. Together that is"
        echo "  what caused an out-of-memory kill on this machine during the build."
        echo
        echo "  Options:"
        echo "    1. run the 8 fast suites instead:   bash run_all_tests.sh"
        echo "    2. stop the model service first:    sudo docker compose stop tagasenti"
        echo "       (you cannot score reports while it is stopped)"
        echo "    3. override this check:             bash run_all_tests.sh --full --force"
        echo "=============================================================================="
        exit 2
    fi
    echo "memory preflight: ${avail_mb} MB available, need ~${NEED_MB} MB - proceeding"
    echo
fi

if [ "$MODE" = "--full" ]; then
    ALL=("${SUITES[@]}" "${SLOW[@]}")
else
    ALL=("${SUITES[@]}")
fi

if [ -n "$ONLY" ]; then
    FILTERED=()
    for row in "${ALL[@]}"; do
        IFS='|' read -r id rest <<< "$row"
        case " $ONLY " in *" $id "*) FILTERED+=("$row");; esac
    done
    if [ ${#FILTERED[@]} -eq 0 ]; then
        echo "No suite matched: $ONLY"
        echo "Run 'bash run_all_tests.sh --list' to see the ids."
        exit 2
    fi
    ALL=("${FILTERED[@]}")
fi

# ---- run ------------------------------------------------------------------
echo "=============================================================================="
echo "Review Pulso - test run   ($(date '+%Y-%m-%d %H:%M:%S'))"
if [ "$MODE" = "--full" ]; then echo "mode: FULL (includes slow model-loading suites)"
else echo "mode: quick  (use --full for the slow ones)"; fi
echo "=============================================================================="

PASSED=0; FAILED=0; FAILED_IDS=()
START_ALL=$(date +%s)

for row in "${ALL[@]}"; do
    IFS='|' read -r id cmd needs <<< "$row"
    t0=$(date +%s)
    out=$($cmd 2>&1)
    rc=$?
    t1=$(date +%s)
    dur=$((t1 - t0))
    summary=$(printf '%s\n' "$out" | grep -oE 'RESULT: .*' | head -1)
    [ -z "$summary" ] && summary=$(printf '%s\n' "$out" | grep -oE '\[OK \].*pages=[0-9]+' | head -1)
    [ -z "$summary" ] && summary="(no summary line; exit $rc)"

    if [ $rc -eq 0 ]; then
        PASSED=$((PASSED + 1))
        printf '  [PASS] %-4s %-46s %3ds\n' "$id" "$summary" "$dur"
    else
        FAILED=$((FAILED + 1)); FAILED_IDS+=("$id")
        printf '  [FAIL] %-4s %-46s %3ds\n' "$id" "$summary" "$dur"
        # Show the first few failing assertions, not the whole log.
        printf '%s\n' "$out" | grep -E '^\s+\[FAIL\]|^\s+\[DIFF\]|FAIL:' | head -5 \
            | sed 's/^/           /'
    fi
done

TOTAL=$(( $(date +%s) - START_ALL ))

echo "=============================================================================="
printf 'RESULT: %d suite(s) passed, %d failed  in %ds\n' "$PASSED" "$FAILED" "$TOTAL"
if [ $FAILED -ne 0 ]; then
    echo "  failed: ${FAILED_IDS[*]}"
    echo
    echo "  A red suite here means the code is wrong. Do not deploy."
    echo "  Fix it, re-run this script, and only then rebuild or re-import."
else
    echo
    echo "  All green. This verifies the CODE and the WIRING."
    echo "  It does NOT verify that the AI is accurate for your business's"
    echo "  reviews - that needs human-labelled data. See ../OPERATIONS.md"
    echo "  section 13, 'Things that are not finished yet'."
    echo
    echo "  Not covered here (they need the containers running):"
    echo "    bash 09_smoke_test.sh     container health, inference, PDF output"
    echo "    bash 14_visual_check.sh   renders the report for visual inspection"
fi
echo "=============================================================================="
exit $([ $FAILED -ne 0 ] && echo 1 || echo 0)
