#!/usr/bin/env bash
# ===========================================================================
#  STEP 21 - Make the reports folder usable by the host user.
#
#  THE PROBLEM
#  Reports come out owned by uid 1000 (the n8n container's `node` user) with
#  mode 644, because n8n creates files with the container's umask 022:
#
#      666 - 022  ->  644        owner rw, group r, other r
#
#  The host user is uid 1001, so it falls into "other" and gets r-x. The
#  directory itself is 777, so the user CAN read, delete, rename, and create -
#  the only thing blocked is editing a PDF in place. File managers also show
#  the owner as "UNKNOWN", because uid 1000 does not exist on the host, and
#  will often mark such files read-only.
#
#  THE FIXES, IN ORDER OF PREFERENCE
#
#  1. BEST - give the n8n container a permissive umask, so files are created
#     666 in the first place. This belongs in docker-compose.yml and is
#     commented out there with instructions, because changing a container's
#     entrypoint cannot be tested from this session (no Docker access) and a
#     broken n8n is a worse outcome than a read-only file.
#
#  2. THIS SCRIPT - fix permissions on files as they arrive. Zero risk: it
#     touches only the report folder, never the containers or the database.
#
#     IMPORTANT: this script MUST run as root, or as uid 1000. Reports are
#     created by n8n as uid 1000, and only the OWNER or root may chmod a file.
#     The first version of this script ran as the host user (uid 1001), could
#     not chmod anything, and silently changed nothing - it reported success
#     because `chmod` returns non-zero but `find -exec` was left to swallow it.
#     It now checks and tells you.
#
#  USAGE
#      sudo bash 21_fix_report_permissions.sh      # one-off
#      sudo crontab -e                             # then add:
#        */5 * * * * /bin/bash <this script> >/dev/null 2>&1
# ===========================================================================
set -uo pipefail

DOWNLOAD_DIR="${DOWNLOAD_DIR:-$HOME/Downloads/review-pulso}"
TARGET_MODE="${TARGET_MODE:-a+rw}"
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"

if [ ! -d "$DOWNLOAD_DIR" ]; then
    echo "FATAL: $DOWNLOAD_DIR does not exist"
    exit 1
fi

# ---- refuse to pretend it worked -----------------------------------------
# chmod only succeeds for the file's owner or for root. If neither holds, the
# run must fail loudly rather than print a reassuring summary.
if [ "$(id -u)" -ne 0 ]; then
    echo "WARNING: running as uid $(id -u), not root."
    echo
    echo "  Reports are created by n8n as uid 1000. Only the OWNER or ROOT can"
    echo "  chmod them, so this run cannot change the REPORTS' permissions."
    echo "  (Your own files are unaffected - you can chmod those as their owner.)"
    echo
    echo "  Re-run with sudo:   sudo bash $SELF"
    echo
    echo "  (You are still able to read, delete, rename and create files in that"
    echo "   folder, because the DIRECTORY is 777 - editing a PDF in place is"
    echo "   the only blocked operation.)"
    echo
fi

# ---- do it ----------------------------------------------------------------
changed=0
failed=0
while IFS= read -r -d '' f; do
    if chmod $TARGET_MODE "$f" 2>/dev/null; then
        changed=$((changed + 1))
    else
        failed=$((failed + 1))
    fi
done < <(find "$DOWNLOAD_DIR" -type f -print0)

chmod a+rwx "$DOWNLOAD_DIR" 2>/dev/null

total=$(find "$DOWNLOAD_DIR" -type f | wc -l)
echo "permissions updated on $changed of $total file(s); $failed failed"
[ "$failed" -gt 0 ] && echo "  (failures mean you lack ownership - re-run with sudo)"
echo
ls -l "$DOWNLOAD_DIR" | tail -4
echo
echo "To run this automatically, use the ROOT crontab (sudo crontab -e) and add:"
echo
echo "  */5 * * * * /bin/bash $SELF >/dev/null 2>&1"
echo
echo "A user crontab will NOT work: it cannot chmod files owned by uid 1000."
echo
echo "The better fix is the container umask - see the comment in"
echo "~/n8n/docker-compose.yml above the N8N_ENCRYPTION_KEY line."
