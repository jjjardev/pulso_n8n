#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Fetch the TagaSenti INT8 ONNX model and its tokenizer.
#
#   bash tagasenti/fetch_model.sh
#
# Both artefacts are large (553 MB combined) and are NOT in git. This script
# downloads them from the upstream Hugging Face repository and verifies a
# pinned sha256 for each, so a corrupt or truncated file fails loudly instead
# of producing quietly wrong sentiment scores.
#
# Upstream:  https://huggingface.co/jjjardev/tagasenti_model
# Weights:   Apache 2.0     Dataset: CC BY-SA 4.0 (not redistributed here)
#
# ---------------------------------------------------------------------------
# TWO DIGEST TRAPS, both hit while writing this script. Read before editing.
#
# 1. The tree API's `lfs.oid` and the resolve endpoint's `ETag` are DIFFERENT
#    values for the same file:
#        lfs.oid   5dd3ce8b9bafb09d02b3...   <- this is the real sha256
#        ETag      1bec969ab4197e413...      <- something else entirely
#    Verified by hashing the model actually in use: it matches lfs.oid. So the
#    digests below are lfs.oid values. Do not "simplify" this by switching to
#    ETag - every download would fail.
#
# 2. The tree API is not reliably reachable unauthenticated (it returned 401
#    on a second call minutes after working). That is why the expected hashes
#    are hardcoded here rather than fetched at run time. A script that cannot
#    verify is worse than one that verifies against a constant.
#
# Only LFS files get 64-hex sha256. The two small JSON files are stored as
# ordinary git blobs and have 40-hex SHA-1 ETags, so their digests below are
# pinned from a local copy of the upstream files.
# ---------------------------------------------------------------------------
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

REPO="jjjardev/tagasenti_model"
BASE="https://huggingface.co/${REPO}/resolve/main"

# filename | human size | sha256
FILES=(
  "tagasenti_int8.onnx|536.75 MB|5dd3ce8b9bafb09d02b3945e021921a771af3b2d7542e513a548b6bdbbf3df21"
  "tokenizer.json|16.31 MB|acbd420e2269cdc1ef45332d3d5c418be4aef6b8cb5a0b7ccae0893485307153"
  "tokenizer_config.json|343 B|5d84c938d902dbd684af806ccc920e4c99812d897b0f271eebf856361a4337f6"
  "config.json|922 B|6cfd39a36139784a1ae98ea589ac1e211d6527ee7c38919273a9460ac0ff7c87"
)

say()  { printf '  %s\n' "$*"; }
fail() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

command -v curl >/dev/null 2>&1 || fail "curl is required but not installed."
command -v sha256sum >/dev/null 2>&1 || fail "sha256sum is required but not installed."

verify() {
  local path="$1" expected="$2" actual
  actual="$(sha256sum "${path}" | cut -d' ' -f1)"
  if [ "$actual" = "$expected" ]; then
    say "$(basename "${path}"): sha256 OK"
  else
    fail "$(basename "${path}"): sha256 MISMATCH
        expected ${expected}
        actual   ${actual}
        Delete the file and re-run this script.
        Do not use an unverified model: wrong weights produce wrong scores
        and report no error anywhere."
  fi
}

echo "TagaSenti model fetch"
echo "  upstream: https://huggingface.co/${REPO}"
echo "  target:   ${HERE}"
echo

# --- already present, but verify anyway ------------------------------------
# A file that exists is not automatically a correct file. A partial download
# is the failure mode that actually happens with a 537 MB transfer.
missing=0
for entry in "${FILES[@]}"; do
  IFS='|' read -r file size expected <<<"$entry"
  if [ -f "${HERE}/${file}" ]; then
    say "${file} present (${size}) - verifying"
    verify "${HERE}/${file}" "$expected"
  else
    missing=1
  fi
done

# --- download what is missing ---------------------------------------------
if [ "$missing" -eq 1 ]; then
  for entry in "${FILES[@]}"; do
    IFS='|' read -r file size expected <<<"$entry"
    [ -f "${HERE}/${file}" ] && continue
    say "downloading ${file} (${size})..."
    # -L follows the redirect to the CDN. --fail turns a 404 into a non-zero
    # exit, so an HTML error page is never saved under a .onnx name.
    if ! curl -fL --progress-bar -o "${HERE}/${file}.part" "${BASE}/${file}"; then
      rm -f "${HERE}/${file}.part"
      fail "${file} could not be downloaded from ${BASE}/${file}"
    fi
    # Verify the .part file BEFORE it becomes the real file, so a bad
    # download never leaves a plausible-looking artefact behind.
    verify "${HERE}/${file}.part" "$expected"
    mv "${HERE}/${file}.part" "${HERE}/${file}"
  done
else
  echo
  say "all files already present and verified."
fi

# --- expose the tokenizer where the service expects it ---------------------
# tagasenti/main.py reads TOKENIZER_DIR, which the Dockerfile sets to
# ./tokenizer. Symlink rather than duplicate the 16 MB.
mkdir -p "${HERE}/tokenizer"
for t in tokenizer.json tokenizer_config.json config.json; do
  [ -f "${HERE}/${t}" ] || continue
  if [ ! -e "${HERE}/tokenizer/${t}" ] || [ -L "${HERE}/tokenizer/${t}" ]; then
    ln -sf "../${t}" "${HERE}/tokenizer/${t}"
  fi
done
say "tokenizer/ ready"

echo
echo "Done. Verify the model loads and the labels map correctly with:"
echo "    python3 agent_journal/01_local_onnx_verification.py"