#!/usr/bin/env bash
# ===========================================================================
#  STEP 20 - Host/container permission check for the downloads mount.
#
#  WHY THIS EXISTS
#  The pipeline failed live with:
#
#      Problem in node 'Node 8 - Save PDF'
#      Forbidden by access permissions, make sure you have the right permissions
#
#  The cause was a uid mismatch that is easy to get wrong and impossible to
#  see from inside the container:
#
#      host user  jjjarder = uid 1001
#      n8n image  node     = uid 1000
#
#  The host directory is owned by 1001. Inside the container, n8n runs as 1000
#  and is therefore "somebody else" in a 775 directory - which grants others
#  r-x and NO write.
#
#  The mistake that produced it: I wrote in the compose file that "n8n runs as
#  uid 1000, which is also the uid of jjjarder", inferred from the n8n_data
#  volume being owned by 1000:1000. That inference is wrong - a volume's files
#  are owned by the CONTAINER user, which says nothing about the host user's
#  uid. The host user is 1001.
#
#  This check is deliberately a shell script rather than part of the Python
#  suite: it inspects host filesystem ownership and the compose file, and it
#  needs the real uids rather than a container it cannot reach.
#
#  USAGE
#      bash 20_check_downloads_permissions.sh
#
#  EXIT
#      0 = the container user can write to the mount
#      1 = it cannot, and the pipeline will fail at Node 8
# ===========================================================================
set -uo pipefail

DOWNLOAD_DIR="${DOWNLOAD_DIR:-$HOME/Downloads/review-pulso}"
COMPOSE="$HOME/n8n/docker-compose.yml"
FAILED=0

pass() { echo "  [PASS] $1"; }
fail() { echo "  [FAIL] $1"; FAILED=$((FAILED+1)); }
info() { echo "         $1"; }

echo "=============================================================================="
echo "Downloads mount - permission check"
echo "dir: $DOWNLOAD_DIR"
echo "=============================================================================="

# --- 1. the directory exists -----------------------------------------------
if [ ! -d "$DOWNLOAD_DIR" ]; then
    fail "the host downloads directory does not exist"
    info "create it:  mkdir -p $DOWNLOAD_DIR && chmod 777 $DOWNLOAD_DIR"
    echo "=============================================================================="
    exit 1
fi
pass "the host downloads directory exists"

# --- 2. THE uid MISMATCH -----------------------------------------------------
HOST_UID=$(stat -c '%u' "$DOWNLOAD_DIR")
HOST_GID=$(stat -c '%g' "$DOWNLOAD_DIR")
MODE=$(stat -c '%a' "$DOWNLOAD_DIR")

# The n8n image's `node` user. Read it from the volume if n8n's data is
# present, else fall back to the documented value for the official image.
CONTAINER_UID=1000
if [ -f "$COMPOSE" ]; then
    info "host uid/gid  : $HOST_UID:$HOST_GID   mode $MODE"
    info "n8n 'node' uid: $CONTAINER_UID (official n8n image; its n8n_data"
    info "                volume files are owned 1000:1000, which is the"
    info "                CONTAINER user, not the host user)"
fi

# --- 3. can uid 1000 actually write? ---------------------------------------
# Computed from the mode rather than guessed. Checks owner, then group, then
# other, in the order the kernel would.
can_write() {
    local target_uid="$1" perms="$2"
    local o g w
    # strip any setuid/sticky digit and pad to 3
    perms="${perms: -3}"
    o=$(( (8#$perms >> 6) & 7 ))
    g=$(( (8#$perms >> 3) & 7 ))
    w=$(( 8#$perms & 7 ))
    if [ "$target_uid" -eq "$HOST_UID" ]; then
        [ $(( o & 2 )) -ne 0 ] && return 0 || return 1
    fi
    # A bind mount does not map the container's supplementary groups to the
    # host's, so group membership cannot be relied on. Only `other` is safe.
    if [ $(( w & 2 )) -ne 0 ]; then return 0; fi
    return 1
}

if can_write "$CONTAINER_UID" "$MODE"; then
    pass "uid $CONTAINER_UID (n8n) CAN write to the mount"
else
    fail "uid $CONTAINER_UID (n8n) CANNOT write - Node 8 will fail with"
    info "'Forbidden by access permissions'"
    info "fix:  chmod 777 $DOWNLOAD_DIR"
fi

# --- 4. the compose mount actually points at this directory -----------------
if [ -f "$COMPOSE" ]; then
    # Match on the CONTAINER side of the volume mapping, which is the part
    # that matters and is written identically either way. The host side may
    # be literal or use compose's ${DOWNLOAD_DIR:-default} substitution, and
    # an earlier version of this check grepped for the literal host path and
    # reported a correctly-configured mount as missing.
    if grep -qE ':/home/node/\.n8n-files/downloads([[:space:]]|$|:ro)' "$COMPOSE"; then
        pass "compose mounts a host dir at /home/node/.n8n-files/downloads"
        grep -oE '^[[:space:]]*-[[:space:]]*[^:]*:/home/node/\.n8n-files/downloads' \
            "$COMPOSE" | sed 's/^/         /'
        if grep -q 'DOWNLOAD_DIR' "$COMPOSE"; then
            info "compose uses \${DOWNLOAD_DIR:-...}; effective host dir is"
            info "$DOWNLOAD_DIR unless the env var overrides it"
        fi
    else
        fail "compose does not mount anything at /home/node/.n8n-files/downloads"
        info "Node 8 and 9 write to that path; without the mount they either"
        info "fail or write into the container's own filesystem, where you"
        info "will never find the report."
    fi
else
    info "compose file not found at $COMPOSE - skipping the mount check"
fi

# --- 5. the workflow's paths are absolute and match ------------------------
WF="$(dirname "${BASH_SOURCE[0]}")/product/review-pulso.workflow.json"
if [ -f "$WF" ]; then
    if grep -q '"/home/node/.n8n-files/downloads/' "$WF" \
       || grep -q '=/home/node/.n8n-files/downloads/' "$WF"; then
        pass "the workflow writes to the absolute sandboxed path"
    else
        fail "the workflow does not use the absolute downloads path"
    fi
else
    info "workflow JSON not found - skipping the path check"
fi

# --- 6. binary storage mode -------------------------------------------------
# The workflow passes a binary through a Code node (Node 7 -> Node 8). That
# only works with n8n's default in-memory binary mode. In filesystem mode a
# binary is an id into a store, and the reference is dropped when a Code node
# returns a new binary map - which fails at Node 8 with
# "The item has no binary field 'data'".
echo
echo "T6. Binary storage mode (Node 7 -> Node 8 binary pass-through)"
if [ -f "$COMPOSE" ]; then
    # Only look at lines that are ACTUAL environment entries (a YAML list item
    # under `environment:`), not prose. An earlier version grepped the whole
    # file and matched the comment that explains the removal, reporting a
    # setting that no longer exists. Same failure class as the mount check.
    mode=$(grep -E "^[[:space:]]*-[[:space:]]*N8N_DEFAULT_BINARY_DATA_MODE=" "$COMPOSE" \
           | head -1 | sed -E 's/^[[:space:]]*-[[:space:]]*//')
    if [ -n "$mode" ]; then
        case "$mode" in
            *=filesystem)
                fail "compose sets $mode - the Code node binary pass-through will FAIL"
                info "remove the line; in-memory is correct for these payload sizes"
                ;;
            *)
                info "compose sets $mode, which is fine"
                pass "binary mode is compatible with the workflow"
                ;;
        esac
    else
        pass "no N8N_DEFAULT_BINARY_DATA_MODE override - n8n uses its default"
        info "(in-memory, which is what Node 7 -> Node 8 requires)"
    fi
else
    info "compose file not found - skipping the binary mode check"
fi

echo "=============================================================================="
if [ "$FAILED" -eq 0 ]; then
    echo "RESULT: the container user can write. Node 8 and 9 should succeed."
else
    echo "RESULT: $FAILED PROBLEM(S). The PDF write will fail until fixed."
fi
echo "=============================================================================="
exit $FAILED
