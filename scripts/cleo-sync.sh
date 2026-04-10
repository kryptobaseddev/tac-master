#!/usr/bin/env bash
# cleo-sync.sh — bidirectional CLEO tasks.db sync between dev machine and LXC
#
# Direction A (primary): dev machine → LXC  (push authoritative copy)
# Direction B (write-back): LXC → dev machine (pull daemon status updates back)
#
# Strategy:
#   1. Compare modification times of dev and LXC copies.
#   2. If LXC copy is NEWER than dev copy (daemon wrote status updates), pull
#      LXC copy to dev first (write-back), then WAL checkpoint the new state.
#   3. WAL checkpoint dev machine copy so the main .db file is complete.
#   4. Push dev machine copy to LXC.
#   5. Write a timestamp file on LXC for staleness tracking.
#
# T035, T072 — bidirectional sync implementation

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CREDS_FILE="$(dirname "$SCRIPT_DIR")/.tac-master-creds.env"
CLEO_DB="/mnt/projects/agentic-engineer/.cleo/tasks.db"
LXC_IP="10.0.10.22"
LXC_USER="root"
LXC_STATE_DIR="/srv/tac-master/state"
LXC_DB_PATH="$LXC_STATE_DIR/cleo-tasks.db"
TS_FILE="$LXC_STATE_DIR/.cleo_sync_ts"
# Track the last mtime we pushed to LXC — write-back happens if LXC is newer than this
LAST_PUSH_TS_FILE="/tmp/cleo_sync_last_push_mtime"
LOG_TAG="cleo-sync"

# Load credentials
if [[ -f "$CREDS_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a && source "$CREDS_FILE" && set +a
fi

# SSH_PASSWORD must be set (from creds file or environment)
if [[ -z "${SSH_PASSWORD:-}" ]]; then
  echo "$LOG_TAG: ERROR — SSH_PASSWORD not set; cannot connect to LXC" >&2
  exit 1
fi

export SSHPASS="$SSH_PASSWORD"
SSH_OPTS="-o StrictHostKeyChecking=no -o BatchMode=no -o PubkeyAuthentication=no"

ssh_lxc() {
  sshpass -e ssh $SSH_OPTS "${LXC_USER}@${LXC_IP}" "$@"
}

rsync_push() {
  sshpass -e rsync -az --inplace --no-perms \
    -e "ssh $SSH_OPTS" \
    "$1" "${LXC_USER}@${LXC_IP}:$2"
}

rsync_pull() {
  sshpass -e rsync -az --inplace --no-perms \
    -e "ssh $SSH_OPTS" \
    "${LXC_USER}@${LXC_IP}:$1" "$2"
}

log() {
  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') $LOG_TAG: $*"
}

# ── Step 1: Check if LXC copy has writes since last push (write-back detection) ──
# We compare the LXC DB mtime against the mtime we recorded at last push.
# If LXC is newer than what we last pushed, it has daemon writes to pull back.
LAST_PUSH_MTIME=0
if [[ -f "$LAST_PUSH_TS_FILE" ]]; then
  LAST_PUSH_MTIME=$(cat "$LAST_PUSH_TS_FILE" 2>/dev/null || echo 0)
fi

# Checkpoint LXC WAL first so committed writes are in the main DB file
# (WAL writes don't update the main file mtime until checkpointed)
ssh_lxc "sqlite3 '$LXC_DB_PATH' 'PRAGMA wal_checkpoint(FULL);' 2>/dev/null || true" 2>/dev/null || true

LXC_MTIME=$(ssh_lxc "stat -c '%Y' '$LXC_DB_PATH' 2>/dev/null || echo 0" 2>/dev/null || echo 0)

if [[ "$LXC_MTIME" -gt "$LAST_PUSH_MTIME" && "$LAST_PUSH_MTIME" -gt "0" ]]; then
  log "LXC has writes since last push (LXC_mtime=${LXC_MTIME} last_push=${LAST_PUSH_MTIME}) — pulling write-back to dev"
  # Pull LXC copy back to dev machine (daemon status updates write-back)
  # WAL was already checkpointed above
  rsync_pull "$LXC_DB_PATH" "${CLEO_DB}.lxc_writeback"

  # WAL checkpoint the pulled copy to make it self-contained
  if command -v sqlite3 &>/dev/null; then
    sqlite3 "${CLEO_DB}.lxc_writeback" "PRAGMA wal_checkpoint(FULL);" 2>/dev/null || true
  fi

  # Replace dev copy with LXC version (write-back complete)
  mv "${CLEO_DB}.lxc_writeback" "$CLEO_DB"
  rm -f "${CLEO_DB}.lxc_writeback-wal" "${CLEO_DB}.lxc_writeback-shm"
  log "Write-back complete — dev copy updated from LXC"
elif [[ "$LAST_PUSH_MTIME" -eq "0" ]]; then
  log "First run — skipping write-back check (no previous push recorded)"
else
  log "LXC copy not newer than last push (LXC_mtime=${LXC_MTIME} last_push=${LAST_PUSH_MTIME}) — no write-back needed"
fi

# ── Step 2: WAL checkpoint dev copy before push ────────────────────────────
if command -v sqlite3 &>/dev/null && [[ -f "$CLEO_DB" ]]; then
  sqlite3 "$CLEO_DB" "PRAGMA wal_checkpoint(FULL);" 2>/dev/null || true
  log "WAL checkpoint complete"
fi

# ── Step 3: Push dev copy to LXC ──────────────────────────────────────────
if [[ -f "$CLEO_DB" ]]; then
  rsync_push "$CLEO_DB" "$LXC_DB_PATH"
  # After a full WAL checkpoint, the -wal file on dev is empty/absent.
  # Remove stale WAL/SHM on LXC to prevent SQLite confusion.
  ssh_lxc "rm -f '${LXC_DB_PATH}-wal' '${LXC_DB_PATH}-shm'" 2>/dev/null || true
  # Fix ownership so krypto user (dashboard server) can read the file
  ssh_lxc "chown krypto:krypto '${LXC_DB_PATH}'" 2>/dev/null || true
  # Record the LXC mtime AFTER our push, so we can detect subsequent LXC writes
  NEW_LXC_MTIME=$(ssh_lxc "stat -c '%Y' '$LXC_DB_PATH' 2>/dev/null || echo 0" 2>/dev/null || echo 0)
  echo "$NEW_LXC_MTIME" > "$LAST_PUSH_TS_FILE"
  log "Push to LXC complete (recorded push mtime: $NEW_LXC_MTIME)"
else
  log "WARNING: dev CLEO_DB not found at $CLEO_DB — skipping push"
fi

# ── Step 4: Write staleness timestamp on LXC ──────────────────────────────
SYNC_TS=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
ssh_lxc "echo '$SYNC_TS' > '$TS_FILE' && chown krypto:krypto '$TS_FILE'" 2>/dev/null || true
log "Sync timestamp written to LXC: $SYNC_TS"
