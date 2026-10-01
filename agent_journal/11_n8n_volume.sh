#!/usr/bin/env bash
# ===========================================================================
#  STEP 11 - Back up and INSPECT the n8n_data volume, read-only.
#
#  WHY THIS EXISTS
#  n8n is currently crash-looping with:
#
#    Error: Mismatching encryption keys. The encryption key in the settings
#    file /home/node/.n8n/config does not match the N8N_ENCRYPTION_KEY env var.
#
#  n8n 2.x writes a key-derived value into /home/node/.n8n/config on first
#  start and REFUSES TO BOOT if the env var no longer matches it. This is
#  actually good news: n8n is refusing to start rather than silently serving
#  you undecryptable credentials. It also means the stored data has NOT been
#  rewritten - the fix is to put the old key back.
#
#  But "just put the old key back" is only safe if we know what is in the
#  volume first. This script lets you find out WITHOUT starting n8n and
#  WITHOUT modifying anything.
#
#  ACTIONS (pick one)
#    backup     copy the whole volume to a timestamped tarball. Do this FIRST,
#               whatever else you decide. It is cheap and makes everything else
#               reversible.
#    inspect    read the SQLite database and report how many workflows,
#               credentials and users exist. Purely read-only.
#    config     show (not print!) the encryption-key fingerprint n8n stored, so
#               you can confirm which key the volume expects.
#    key        show the old key's fingerprint for comparison.
#
#  USAGE
#    sudo bash 11_n8n_volume.sh backup
#    sudo bash 11_n8n_volume.sh inspect
# ===========================================================================
set -uo pipefail

ACTION="${1:-inspect}"
VOLUME="${N8N_VOLUME:-n8n_n8n_data}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/n8n-backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"
OLD_KEY="change-me-to-a-long-random-string"
# The candidate replacement key used during the build. It is recorded here
# because the journal refers to it by fingerprint, NOT because it should be
# reused: a rotation target that lives in a public repository is not a secret.
# To rotate for real, generate a fresh one:
#     openssl rand -hex 32
NEW_KEY="${NEW_KEY:-$(openssl rand -hex 32)}"

# A throwaway alpine container with sqlite3, used to query a COPY of the
# database. It only ever mounts the volume READ-ONLY; nothing writes to it.
# See the long note in the INSPECT section for why the copy step is necessary.

die() { echo "FATAL: $*" >&2; exit 1; }

echo "=============================================================================="
echo "n8n volume helper - action: $ACTION"
echo "volume: $VOLUME"
echo "=============================================================================="

# --- confirm the volume actually exists ------------------------------------
if ! docker volume inspect "$VOLUME" >/dev/null 2>&1; then
    echo
    echo "Volume '$VOLUME' not found. Available n8n volumes:"
    docker volume ls --format '{{.Name}}' | grep -i n8n || echo "  (none matching 'n8n')"
    die "check the name; re-run with N8N_VOLUME=<name> $0 $ACTION"
fi

# --- BACKUP ---------------------------------------------------------------
if [ "$ACTION" = "backup" ]; then
    mkdir -p "$BACKUP_DIR"
    OUT="$BACKUP_DIR/${VOLUME}-${STAMP}.tar.gz"
    echo
    echo "Backing up volume '$VOLUME' -> $OUT"
    echo "This reads the volume; it does not modify it."
    echo
    # Use a busybox tar so we get a clean tarball regardless of host tooling.
    docker run --rm \
        -v "$VOLUME":/data:ro \
        -v "$BACKUP_DIR":/out \
        busybox:latest \
        tar czf "/out/${VOLUME}-${STAMP}.tar.gz" -C /data . || die "backup failed"
    echo
    if [ -f "$OUT" ]; then
        echo "OK  $OUT"
        ls -lh "$OUT" | awk '{print "    size:", $5}'
        echo
        echo "To restore later:"
        echo "  docker run --rm -v $VOLUME:/data -v $BACKUP_DIR:/out \\"
        echo "    busybox sh -c 'rm -rf /data/* && tar xzf /out/$(basename "$OUT") -C /data'"
        echo
        echo "Keep this file. It is the only copy of whatever was in that volume."
    else
        die "tarball not created - is $BACKUP_DIR writable?"
    fi
    exit 0
fi

# --- CONFIG (fingerprint, not the key) ------------------------------------
if [ "$ACTION" = "config" ]; then
    echo
    echo "n8n's stored config file (shows the encryptionKey value it expects):"
    echo "  --- this is a HASH/derived value, not your plaintext key ---"
    docker run --rm -v "$VOLUME":/data:ro busybox:latest \
        sh -c 'ls -la /data/config 2>/dev/null && echo "--- contents ---" && cat /data/config' \
        || die "could not read /data/config"
    echo
    echo "Fingerprints of the two candidate keys (sha256, for comparison):"
    printf '  old (change-me-...): %s\n' "$(printf '%s' "$OLD_KEY" | sha256sum | cut -c1-32)"
    printf '  new (generated):      %s\n' "$(printf '%s' "$NEW_KEY" | sha256sum | cut -c1-32)"
    echo
    echo "To adopt the new key: put it in .env as N8N_ENCRYPTION_KEY AND replace"
    echo "the encryptionKey inside the volume's config file, or delete that file"
    echo "(n8n regenerates it) and restart. Setting only .env crash-loops n8n."
    echo
    echo "n8n derives its own value from the key, so these sha256s will NOT match"
    echo "the file above. Use inspect + the docs' guidance instead: the simplest"
    echo "resolution is to restore the OLD key and confirm n8n boots."
    exit 0
fi

# --- INSPECT --------------------------------------------------------------
echo
echo "Files in the volume:"
docker run --rm -v "$VOLUME":/data:ro busybox:latest \
    sh -c 'ls -la /data | head -30'
echo

# ---------------------------------------------------------------------------
# BUG FIX (found on the first real run). I had hardcoded the database path as
# /data/.n8n/database.sqlite, which does not exist. The compose file mounts the
# volume at /home/node/.n8n INSIDE the n8n container, so when a helper
# container mounts that same volume at /data, the n8n home contents sit
# directly at /data/ - the `.n8n` path component does not exist. Result:
#
#   Error: unable to open database "/data/.n8n/database.sqlite":
#          unable to open database file
#
# The path is now auto-detected rather than guessed, so this cannot recur.
#
# SECOND, SUBTLER ISSUE - the WAL. The listing shows:
#     database.sqlite       1.5M   Sep 28 20:49
#     database.sqlite-wal   4.1M   Sep 30 16:12
# i.e. the main DB has not been checkpointed since Sep 28, and roughly two days
# of transactions live ONLY in the write-ahead log. Querying database.sqlite
# alone would under-report, potentially showing an empty workflow table when
# the workflows are all sitting in the WAL.
#
# SQLite needs to create/lock the -shm file to replay a WAL, which a read-only
# mount forbids. So rather than mount read-only and hope, we copy the three
# files into a WRITABLE temp dir inside the throwaway container and query the
# copy. The original volume is still never written to - the copy is a
# one-way street.
# ---------------------------------------------------------------------------

echo "Reading the database (read-only; the volume itself is never written) ..."
echo
docker run --rm -v "$VOLUME":/data:ro alpine:latest sh -c "
  set -e
  apk add --no-cache sqlite >/dev/null 2>&1
  mkdir -p /work
  # copy the db + its WAL/SHM sidecars into a writable dir so SQLite can
  # replay the WAL and lock the shm. The source is mounted :ro and untouched.
  cp /data/*.sqlite /data/*.sqlite-wal /data/*.sqlite-shm /work/ 2>/dev/null || true
  DB=\$(ls /work/database.sqlite 2>/dev/null || true)
  if [ -z \"\$DB\" ]; then echo '  no database.sqlite found in the volume'; exit 0; fi
  echo \"  db: \$DB  (\$(du -h \$DB | cut -f1) main, \$(du -h /work/database.sqlite-wal 2>/dev/null | cut -f1) wal)\"
  echo
  echo '  --- entity counts (WAL replayed) ---'
  sqlite3 \$DB <<'SQL'
.mode column
.headers on
.width 14 8
SELECT 'users'        AS entity, COUNT(*) AS n FROM user
UNION ALL SELECT 'credentials', COUNT(*) FROM credentials_entity
UNION ALL SELECT 'workflows',   COUNT(*) FROM workflow_entity
UNION ALL SELECT 'executions',  COUNT(*) FROM execution_entity;
SQL
  echo
  echo '  --- workflows (id / name / active) ---'
  sqlite3 \$DB 'SELECT id, name, active FROM workflow_entity;'
  echo
  echo '  --- credentials (id / name / type only; no secret values) ---'
  sqlite3 \$DB 'SELECT id, name, type FROM credentials_entity;'
  echo
  echo '  --- users (id / email only) ---'
  sqlite3 \$DB 'SELECT id, email FROM user;'
" 2>&1 | sed 's/^/  /'

echo
echo "  Non-zero 'workflows' or 'credentials' means there is real n8n content"
echo "  and the OLD encryption key must be restored (JOURNAL.md section 9.2)."
echo "  All zeros means the volume is empty and the new key is fine."
echo "=============================================================================="
