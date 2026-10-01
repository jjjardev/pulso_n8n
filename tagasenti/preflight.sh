#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Preflight: check the things that make `docker compose up --build` fail with
# an error that does not explain itself.
#
#   bash tagasenti/preflight.sh
#
# WHY THIS EXISTS
# The image build does `COPY main.py tagasenti_int8.onnx ./` and
# `COPY tokenizer/ ./tokenizer/`. Both the model and the tokenizer are
# gitignored, because they are 553 MB and reproducible from a pinned sha256.
# So on a fresh clone the build fails with:
#
#     failed to compute cache key: ... "/tokenizer": not found
#
# which names a missing directory and says nothing about the fix. Verified on a
# clean clone: the only way through is to run fetch_model.sh first.
#
# This script checks that, plus the other two things that stop a first run, and
# tells you what to do about each. It changes nothing.
# ---------------------------------------------------------------------------
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || { echo "FATAL: cannot cd to $REPO"; exit 1; }

fail=0
ok()   { printf '  [ ok ] %s\n' "$*"; }
bad()  { printf '  [FAIL] %s\n' "$*"; fail=1; }

echo "preflight"
echo "  repo: $REPO"
echo

# --- 1. model present? -----------------------------------------------------
if [ -e tagasenti/tagasenti_int8.onnx ]; then
  # -L because the file is often a symlink (e.g. pointing at an existing
  # runtime copy); plain `du` reports 0 for a symlink and looked like a
  # zero-byte model.
  size=$(du -hL tagasenti/tagasenti_int8.onnx 2>/dev/null | cut -f1)
  ok "model present (${size:-unknown size})"
else
  bad "model missing - run:  bash tagasenti/fetch_model.sh"
fi

# --- 2. tokenizer present? -------------------------------------------------
# fetch_model.sh symlinks these; a symlink that dangles fails the COPY just as
# hard as an absent file, so check readability rather than existence.
tok_ok=1
for f in tokenizer.json tokenizer_config.json; do
  if [ ! -r "tagasenti/tokenizer/$f" ]; then
    tok_ok=0
  fi
done
if [ "$tok_ok" -eq 1 ]; then
  ok "tokenizer present and readable"
else
  bad "tokenizer missing or dangling - run:  bash tagasenti/fetch_model.sh"
fi

# --- 3. .env present? ------------------------------------------------------
# docker-compose.yml uses ${N8N_ENCRYPTION_KEY:?...} so compose REFUSES to start
# without it, rather than silently encrypting under an empty key. That is
# deliberate; this just tells you where the value comes from.
if [ -f .env ]; then
  if grep -q '^N8N_ENCRYPTION_KEY=change-me' .env 2>/dev/null; then
    printf '  [warn] N8N_ENCRYPTION_KEY is still the placeholder.\n'
    printf '         Harmless while n8n holds zero credentials. Generate one with\n'
    printf '         `openssl rand -hex 32` and paste it into .env before you add any.\n'
  else
    ok ".env present with a custom encryption key"
  fi
else
  bad ".env missing - run:  cp .env.example .env   then set N8N_ENCRYPTION_KEY"
fi

# --- 4. output directory? --------------------------------------------------
# The container user is uid 1000. If the host directory is not group/other
# writable, Node 8 fails at the very end of a long run - after all the
# inference work has already been paid for.
OUT="${DOWNLOAD_DIR:-$HOME/Downloads/review-pulso}"
if [ -d "$OUT" ]; then
  mode=$(stat -c '%a' "$OUT" 2>/dev/null || echo '?')
  case "$mode" in
    *[2367]|???[2367]|????[2367])
      ok "output directory exists and is writable by others (${OUT}, mode ${mode})" ;;
    *)
      printf '  [warn] %s is mode %s. n8n runs as uid 1000 and writes there as\n' "$OUT" "$mode"
      printf '         "other", so it needs a group or other write bit:\n'
      printf '         sudo chown 1000:1000 %s\n' "$OUT"
      fail=1 ;;
  esac
else
  bad "output directory missing: $OUT - run:  mkdir -p '$OUT'"
fi

# --- 5. port clash with another stack? -------------------------------------
# Both n8n and the uploader bind loopback ports. A second copy of this project
# - including a clone, since compose derives the project name from the directory
# - will fail to bind them.
#
# The distinction that matters: ports held by THIS project's own already-running
# stack are not a clash, they are the desired state. Reporting those as a
# failure makes preflight useless as a health check, which is how the README
# presents it - it told the operator "port 5678 is already in use by another
# container" while their own four services were running and healthy.
if command -v docker >/dev/null 2>&1; then
  # Which compose project is actually serving Review Pulso right now? Not
  # necessarily THIS directory: Compose names the project after the directory,
  # so a deployment made from an older checkout elsewhere has a different
  # project name even though it is the same application. Asking this directory
  # alone reported "held by a stack that is NOT this project" while the user's
  # own four services were healthy - the same misidentification that broke
  # 09_smoke_test.sh.
  running_project() {
    local dir
    for dir in "$REPO" "$HOME/n8n"; do
      [ -f "$dir/docker-compose.yml" ] || continue
      if docker compose -f "$dir/docker-compose.yml" ps --services 2>/dev/null \
          | grep -qx tagasenti; then
        printf '%s' "$dir"
        return 0
      fi
    done
    return 1
  }

  serving="$(running_project || true)"

  if [ -z "$serving" ]; then
    ok "no Review Pulso stack is running - ports should be free"
    printf '         Start it with:  sudo docker compose up -d --build\n'
  else
    ok "a Review Pulso stack is running from ${serving}"
    if [ "$serving" != "$REPO" ]; then
      printf '         note: that is a different checkout from this directory, so\n'
      printf '         `docker compose` commands run HERE manage a separate project.\n'
    fi
    clash=0
    for p in 5678 8080; do
      if docker ps --format '{{.Ports}}' 2>/dev/null | grep -q ":$p->"; then
        ok "port $p is held by that stack (expected)"
      else
        bad "port $p is NOT bound, though the stack claims to be running"
        clash=1
      fi
    done
    [ "$clash" -eq 0 ] && printf '         To restart:  cd %s && sudo docker compose down && sudo docker compose up -d --build\n' "$serving"
  fi
fi

echo
if [ "$fail" -eq 0 ]; then
  echo "preflight passed. Next:  sudo docker compose up -d --build"
else
  echo "preflight found problems above. Fix them, then:"
  echo "  bash tagasenti/preflight.sh"
  echo "  sudo docker compose up -d --build"
fi
exit "$fail"