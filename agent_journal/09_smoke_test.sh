#!/usr/bin/bash
# ===========================================================================
#  STEP 8 - Post-build smoke test. Run AFTER `docker compose up -d --build`.
#
#  WHAT THIS CHECKS, IN ORDER OF IMPORTANCE
#    1. All three containers are up and healthy.
#    2. tagasenti answers a Tagalog question with the CORRECT label. This is
#       the end-to-end proof that the model, tokenizer, label order and HTTP
#       layer are all wired together inside the container - every one of which
#       could silently differ from what we tested on the host.
#    3. Gotenberg converts our exact HTML to a PDF, and the PDF is A4
#       portrait with backgrounds printed.
#    4. Container-to-container DNS: n8n can actually reach both services by
#       the name used in the workflow URLs. This is the check that catches a
#       missing/renamed network in docker-compose.yml, which is invisible
#       until a client uploads a file.
#
#  WHY THE CURL COMMANDS LOOK ODD
#    tagasenti (8000) and gotenberg (3000) are deliberately NOT published to
#    the host - see the security note in the pipeline doc section 9. So
#    `curl localhost:8000` from your shell will NOT work, despite what the
#    doc's section 2 suggests. Everything below therefore runs from INSIDE the
#    compose network, via `docker compose exec`.
#
#  USAGE
#    cd ~/n8n
#    bash ~/Desktop/PROJECT_with_deps/pulso_n8n/agent_journal/08_smoke_test.sh
# ===========================================================================
set -uo pipefail

JOURNAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# BUG FIX (found on first real run): this script was invoked as
#   sudo bash 09_smoke_test.sh
# and `sudo` resets $HOME to /root, so COMPOSE_DIR resolved to /root/n8n and
# the script died with "FATAL: cannot cd to /root/n8n".
#
# The compose directory has to be the REAL user's home, not root's. SUDO_USER
# is set by sudo precisely for this case, so use it to look up the real home
# directory from /etc/passwd. Falls back sensibly when not run under sudo.
# ---------------------------------------------------------------------------
# That block used to look up the REAL user's home via SUDO_USER. It is no
# longer needed: COMPOSE_DIR is now derived from this script's own location,
# which is the same directory whether or not sudo rewrote $HOME. The sudo
# hazard is therefore gone rather than merely worked around.
# ---------------------------------------------------------------------------
COMPOSE_DIR="${COMPOSE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
FAILED=0

pass() { echo "  [PASS] $1"; }
fail() { echo "  [FAIL] $1"; FAILED=$((FAILED+1)); }
info() { echo "         $1"; }

cd "$COMPOSE_DIR" || { echo "FATAL: cannot cd to $COMPOSE_DIR"; exit 1; }

echo "=============================================================================="
echo "Review Pulso - post-build smoke test"
echo "compose dir: $COMPOSE_DIR"
echo "=============================================================================="

# --- 1. container state ----------------------------------------------------
echo
echo "T1. Container state"
for svc in n8n tagasenti gotenberg; do
    state=$(docker compose ps --format '{{.Service}} {{.State}} {{.Health}}' "$svc" 2>/dev/null | head -1)
    if [ -z "$state" ]; then
        fail "$svc is not running (docker compose ps shows nothing)"
    else
        info "$state"
        case "$state" in
            *"running"*) pass "$svc is running" ;;
            *) fail "$svc is not running: $state" ;;
        esac
    fi
done

# tagasenti and gotenberg are not published, so confirm we truly cannot reach
# them from the host. If someone later "helpfully" adds ports 8000/3000, this
# test should notice and complain, because that is a security regression.
#
# BUG FIX (found on the first real run): this originally used
#     docker compose port tagasenti 8000 || docker compose port gotenberg 8000
# and reported "port 8000 IS published" on a stack that was, in fact,
# correctly configured. This Compose version exits 0 even when the service has
# no published port, so `>/dev/null 2>&1` swallowed the real answer and the
# `||` chain succeeded on an empty result. Both ports were verified closed by
# an actual TCP connect, so the FAIL was entirely the test's fault.
#
# Two independent, reliable checks replace it:
#   1. `docker inspect .HostConfig.PortBindings` - null/{} when nothing is
#      published. This is the authoritative source.
#   2. An actual TCP connect from the host. This is the strongest evidence,
#      because it tests the property we actually care about - reachability -
#      rather than a field in a JSON blob.
echo
echo "T2. Ports 8000/3000 are NOT published to the host (security requirement)"
for svc in tagasenti gotenberg; do
    # --- check 1: the container's own port bindings ---
    bindings=$(docker inspect --format '{{json .HostConfig.PortBindings}}' \
        "$(docker compose ps -q "$svc")" 2>/dev/null)
    case "$bindings" in
        null|"{}"|"") pass "$svc has no host port bindings ($bindings)" ;;
        *) fail "$svc HAS host port bindings: $bindings" ;;
    esac

    # --- check 2: can the host actually connect? (the real test) ---
    for port in $(docker inspect --format \
            '{{range $p, $conf := .NetworkSettings.Ports}}{{$p}} {{end}}' \
            "$(docker compose ps -q "$svc")" 2>/dev/null); do
        if timeout 5 bash -c "cat < /dev/null > /dev/tcp/127.0.0.1/$port" 2>/dev/null; then
            fail "$svc port $port is REACHABLE from the host - not isolated!"
        else
            pass "$svc port $port refuses connections from the host (isolated)"
        fi
    done
done

# Explicit negative control: n8n IS published on 5678, so the same probe must
# SUCCEED there. Without this, T2 would "pass" even if the probe itself were
# broken and could never succeed.
if timeout 5 bash -c 'cat < /dev/null > /dev/tcp/127.0.0.1/5678' 2>/dev/null; then
    pass "negative control: port 5678 (n8n) IS reachable, so the probe works"
else
    fail "negative control FAILED: 5678 is not reachable, so the T2 probe above"
    info "is not proving anything (n8n is supposed to be published on 5678)"
fi

# --- 3. tagasenti, from inside the network ---------------------------------
echo
echo "T3. tagasenti /health (from inside the compose network)"
if health=$(docker compose exec -T tagasenti \
        curl -fsS http://localhost:8000/health 2>/dev/null); then
    pass "tagasenti is healthy"
    info "$health"
    case "$health" in
        *'"Negative","Neutral","Positive"'*)
            pass "label order is Negative/Neutral/Positive" ;;
        *) fail "label order unexpected in /health response" ;;
    esac
else
    fail "tagasenti /health did not respond"
    info "check: docker compose logs tagasenti | tail -50"
fi

echo
echo "T4. tagasenti end-to-end inference (the critical check)"
# Three cases with unambiguous expected labels, mirroring the doc's checklist.
run_predict() {
    local text="$1"
    docker compose exec -T tagasenti curl -fsS -X POST \
        http://localhost:8000/predict \
        -H 'Content-Type: application/json' \
        -d "$(printf '%s' "$text" | python3 -c 'import json,sys; print(json.dumps({"text": sys.stdin.read()}))')" \
        2>/dev/null
}

check_label() {
    local text="$1" expected="$2" resp
    resp=$(run_predict "$text")
    if [ -z "$resp" ]; then
        fail "no response for: $text"
        return
    fi
    if printf '%s' "$resp" | grep -q "\"$expected\""; then
        pass "expected $expected  <- $text"
        info "$(printf '%s' "$resp" | head -c 160)"
    else
        fail "expected $expected but got: $resp   <- $text"
    fi
}

check_label "Ang ganda ng quality ng tela, worth it ang price!" "Positive"
check_label "Wala pa ring update ang order ko hanggang ngayon." "Negative"
check_label "Sa aking palagay, hindi naman ito gaanong importante." "Neutral"

# --- 4. batch endpoint + ordering -----------------------------------------
echo
echo "T5. tagasenti /predict_batch (the endpoint n8n Node 4 actually calls)"
batch_resp=$(docker compose exec -T tagasenti curl -fsS -X POST \
    http://localhost:8000/predict_batch \
    -H 'Content-Type: application/json' \
    -d '{"texts":["Ang ganda ng quality ng tela, worth it ang price!","Wala pa ring update ang order ko hanggang ngayon.","Sulit na sulit, babalik ulit ako dito pramis"]}' 2>/dev/null)
if [ -z "$batch_resp" ]; then
    fail "/predict_batch returned nothing"
    info "check: docker compose logs tagasenti | tail -50"
else
    info "$(printf '%s' "$batch_resp" | head -c 300)"
    n=$(printf '%s' "$batch_resp" | grep -o '"label"' | wc -l)
    if [ "$n" -eq 3 ]; then
        pass "returned 3 results for 3 inputs (order preserved)"
    else
        fail "expected 3 results, got $n"
    fi
    # First input was positive and must come back first.
    first=$(printf '%s' "$batch_resp" | grep -o '"label":"[A-Za-z]*"' | head -1)
    if printf '%s' "$first" | grep -q Positive; then
        pass "result order matches input order (Positive first)"
    else
        fail "result order looks wrong: $first"
    fi
fi

# --- 5. Gotenberg ---------------------------------------------------------
echo
echo "T6. Gotenberg converts the sample report HTML to A4 PDF"
# 07_render_sample_report.py writes into evidence/, and the committed sample
    # lives there. These paths pointed at the directory root, so T6 failed with
    # "missing" on a file that was present.
    HTML="$JOURNAL_DIR/evidence/sample-report.html"
PDF="$JOURNAL_DIR/evidence/smoke-test-report.pdf"
if [ ! -f "$HTML" ]; then
    fail "$HTML missing - run: python3 07_render_sample_report.py"
elif ! docker compose exec -T gotenberg true 2>/dev/null; then
    fail "cannot exec into gotenberg container"
else
    # Copy the HTML in, then post it with EXACTLY Node 6's form fields.
    docker compose cp "$HTML" gotenberg:/tmp/index.html >/dev/null 2>&1
    if docker compose exec -T gotenberg curl -fsS -X POST \
        http://localhost:3000/forms/chromium/convert/html \
        -H 'Gotenberg-Output-Filename: smoke.pdf' \
        --form files=@/tmp/index.html \
        --form printBackground=true \
        --form preferCssPageSize=true \
        --form paperWidth=8.27 --form paperHeight=11.69 \
        --form marginTop=0 --form marginBottom=0 \
        --form marginLeft=0 --form marginRight=0 \
        -o /tmp/smoke.pdf >/dev/null 2>&1 && \
       docker compose exec -T gotenberg sh -c '[ -s /tmp/smoke.pdf ]' 2>/dev/null; then

        docker compose cp gotenberg:/tmp/smoke.pdf "$PDF" >/dev/null 2>&1
        size=$(stat -c%s "$PDF" 2>/dev/null || echo 0)
        if [ "$size" -gt 5000 ]; then
            pass "Gotenberg produced a PDF ($size bytes) -> $PDF"
            # Page size check: A4 portrait is 595x842 pt. A Letter page would
            # be 612x792. Parse the MediaBox out of the raw PDF.
            box=$(grep -a -o 'MediaBox *\[[^]]*\]' "$PDF" | head -1)
            info "MediaBox: ${box:-not found}"
            case "$box" in
                *"[0 0 595"*|*"[0 0 594"*|*"595 "*)
                    pass "page is A4 portrait (595pt wide)" ;;
                *)
                    fail "page does not look A4 portrait: $box"
                    info "if preferCssPageSize is not set, Gotenberg uses Letter" ;;
            esac

            # PAGE COUNT.
            #
            # Added because the report came back as TWO pages against a spec
            # that says one, and the A4 check above could not catch it: a
            # 2-page document is still A4, so T6 passed while the spec was
            # being violated. A check that cannot see the failure mode it
            # looks like it covers is worse than no check.
            #
            # Counted with python3 rather than grep. `grep -o '/Type */Page[^s]'`
            # looks right and silently returns 0, because the /Type /Page token
            # sits at the END OF A LINE in the PDF and grep patterns cannot
            # match across a newline. That reported 0 pages for a document
            # that had 1. python3 is already a dependency of this script.
            #
            # Verified non-vacuous: a deliberately over-tall 3-block document
            # rendered to 2 pages is correctly counted as 2 by this expression.
            pages=$(python3 -c "
import re, sys
d = open('$PDF','rb').read()
print(len(re.findall(rb'/Type\s*/Page[^s]', d)))
" 2>/dev/null || echo -1)
            info "page count: $pages"
            if [ "$pages" -eq 1 ]; then
                pass "report fits on ONE page (the spec says one page)"
            else
                fail "report is $pages pages - the spec says ONE page"
                info "see 13_fit_one_page.py; the fix was a 2-column card"
                info "layout, NOT smaller fonts. See JOURNAL.md."
            fi
        else
            fail "PDF is only $size bytes - Gotenberg probably returned an error"
            docker compose exec -T gotenberg cat /tmp/smoke.pdf 2>/dev/null | head -c 1000
        fi
    else
        fail "Gotenberg conversion failed"
        info "check: docker compose logs gotenberg | tail -50"
    fi
fi

# --- 6. container-to-container reachability -------------------------------
echo
echo "T7. n8n can reach both services by the names used in the workflow"
# This is the check that catches a broken compose network. Node 4 uses
# http://tagasenti:8000 and Node 6 uses http://gotenberg:3000.
for target in "tagasenti:8000" "gotenberg:3000"; do
    if docker compose exec -T n8n curl -fsS --max-time 10 "http://$target/health" >/dev/null 2>&1; then
        pass "n8n -> http://$target/health reachable"
    else
        # n8n's image may not ship curl; fall back to a node one-liner.
        if docker compose exec -T n8n node -e \
            "require('http').get('http://$target/health',r=>process.exit(0)).on('error',()=>process.exit(1))" \
            >/dev/null 2>&1; then
            pass "n8n -> http://$target/health reachable (via node)"
        else
            fail "n8n CANNOT reach http://$target - the workflow URLs will fail"
            info "check the 'networks:' key in docker-compose.yml"
        fi
    fi
done

# --- summary --------------------------------------------------------------
echo
echo "=============================================================================="
if [ "$FAILED" -eq 0 ]; then
    echo "RESULT: ALL SMOKE TESTS PASSED"
    echo "Next: import the workflow JSON, activate, upload the 5-row test CSV."
else
    echo "RESULT: $FAILED CHECK(S) FAILED"
    echo "Do not wire up n8n until these pass."
fi
echo "=============================================================================="
exit $FAILED
