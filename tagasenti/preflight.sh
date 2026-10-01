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
if command -v docker >/dev/null 2>&1; then
  clash=0
  for p in 5678 8080; do
    if docker ps --format '{{.Ports}}' 2>/dev/null | grep -q ":$p->"; then
      bad "port $p is already in use by another container."
      clash=1
    fi
  done
  if [ "$clash" -eq 0 ]; then
    ok "ports 5678 and 8080 are free"
  else
    printf '         Stop the other stack first:\n'
    printf '           cd <other checkout> && sudo docker compose down\n'
    printf '         Compose derives the project name from the directory name, so a\n'
    printf '         second checkout is a SEPARATE project and will not share a volume.\n'
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