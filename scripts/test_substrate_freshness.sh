#!/usr/bin/env bash
# @task T014
# @epic T002
# @why Closes the verification loop on bug #2's copytree fix from session 2026-04-08->09
# @what Proves substrate re-copy propagates source changes on next sync without manual intervention
#
# test_substrate_freshness.sh
# -------------------------------------------------------------------
# Integration test: verifies that RepoManager._inject_substrate (called
# inside sync()) unconditionally re-copies substrate files from the
# source at /srv/tac-master/adws/ into the existing clone at
# /srv/tac-master/repos/<slug>/adws/ — proving that no manual clone
# deletion is needed after a substrate change.
#
# Usage (from /srv/tac-master as krypto or root):
#   bash scripts/test_substrate_freshness.sh
#
# Exit codes:
#   0  — assertion passed (re-copy confirmed)
#   1  — assertion failed or unexpected error
# -------------------------------------------------------------------

set -euo pipefail

TAC_HOME="${TAC_MASTER_HOME:-/srv/tac-master}"
CANARY_TEXT="# T014 canary 2026-04-09"
SUBSTRATE_SRC="${TAC_HOME}/adws/adw_modules/agent.py"
CLONE_SLUG="kryptobaseddev_tac-master"
CLONE_FILE="${TAC_HOME}/repos/${CLONE_SLUG}/adws/adw_modules/agent.py"
PYTHON="${PYTHON:-python3}"

# ------------------------------------------------------------------
# 0. Pre-flight checks
# ------------------------------------------------------------------
echo "[T014] Starting substrate freshness test"
echo "[T014] TAC_HOME   = ${TAC_HOME}"
echo "[T014] SOURCE     = ${SUBSTRATE_SRC}"
echo "[T014] CLONE      = ${CLONE_FILE}"

if [[ ! -f "${SUBSTRATE_SRC}" ]]; then
    echo "[FAIL] Source substrate file not found: ${SUBSTRATE_SRC}"
    exit 1
fi

if [[ ! -f "${CLONE_FILE}" ]]; then
    echo "[FAIL] Clone substrate file not found: ${CLONE_FILE}"
    echo "       Run a dispatch first to create the clone, then re-run this test."
    exit 1
fi

# ------------------------------------------------------------------
# 1. Record baseline — clone must NOT already contain canary
# ------------------------------------------------------------------
if grep -qF "${CANARY_TEXT}" "${CLONE_FILE}"; then
    # Leftover canary from a previous failed run — remove it from source
    # and re-inject before proceeding (idempotency guard).
    echo "[T014] Found stale canary in clone; clearing source first"
    sed -i "/${CANARY_TEXT}/d" "${SUBSTRATE_SRC}"
fi

BASELINE_MTIME=$(stat -c '%Y' "${CLONE_FILE}")
echo "[T014] Clone agent.py baseline mtime = ${BASELINE_MTIME}"

# ------------------------------------------------------------------
# 2. Inject canary into SOURCE substrate file
# ------------------------------------------------------------------
echo "[T014] Injecting canary into source: ${SUBSTRATE_SRC}"
printf '\n%s\n' "${CANARY_TEXT}" >> "${SUBSTRATE_SRC}"

CLEANUP_NEEDED=1
cleanup() {
    if [[ "${CLEANUP_NEEDED:-0}" == "1" ]]; then
        echo "[T014] Reverting source substrate file"
        sed -i "/${CANARY_TEXT}/d" "${SUBSTRATE_SRC}"
        CLEANUP_NEEDED=0
    fi
}
trap cleanup EXIT

# ------------------------------------------------------------------
# 3. Call RepoManager.sync() programmatically via uv
#    This exercises the exact production code path:
#      sync() -> _inject_substrate() -> shutil.rmtree + copytree
# ------------------------------------------------------------------
echo "[T014] Calling RepoManager.sync() via uv run python ..."

PYTHON_SNIPPET=$(cat <<'PYEOF'
import sys
from pathlib import Path
from orchestrator.repo_manager import RepoManager, RepoHandle

home = Path("/srv/tac-master")
rm = RepoManager(
    home=home,
    repos_dir=home / "repos",
    trees_dir=home / "trees",
    identity={"name": "CleoAgent", "email": "cleo@tac-master.local"},
)

# Build a minimal handle for the existing clone
slug = "kryptobaseddev/tac-master"
fs_slug = "kryptobaseddev_tac-master"
handle = RepoHandle(
    url="https://github.com/kryptobaseddev/tac-master",
    slug=slug,
    fs_slug=fs_slug,
    clone_path=home / "repos" / fs_slug,
)

# sync() calls _inject_substrate internally
rm.sync(handle)
print("sync() completed successfully")
PYEOF
)

cd "${TAC_HOME}"
# The Python sync() call must run as krypto (matches production daemon context).
# The source substrate file is root-owned, so the canary injection above requires root.
# This script is designed to be invoked as root; the sync() subprocess drops to krypto.
if [[ "$(id -u)" != "0" ]]; then
    echo "[ERROR] This script must be run as root (substrate source files are root-owned)."
    echo "        sudo bash scripts/test_substrate_freshness.sh"
    exit 1
fi

echo "${PYTHON_SNIPPET}" | sudo -u krypto bash -lc "cd ${TAC_HOME} && uv run python -"

# ------------------------------------------------------------------
# 4. Assert the clone now contains the canary
# ------------------------------------------------------------------
echo "[T014] Verifying canary propagated to clone ..."
if grep -qF "${CANARY_TEXT}" "${CLONE_FILE}"; then
    echo "[PASS] Canary found in clone after sync(). Substrate re-copy is working."
    RESULT=0
else
    echo "[FAIL] Canary NOT found in clone after sync()."
    echo "       This indicates _inject_substrate is not re-copying on sync()."
    echo "       Investigate orchestrator/repo_manager.py lines ~184-204."
    RESULT=1
fi

# ------------------------------------------------------------------
# 5. Check post-sync mtime changed (sanity)
# ------------------------------------------------------------------
NEW_MTIME=$(stat -c '%Y' "${CLONE_FILE}")
if [[ "${NEW_MTIME}" -gt "${BASELINE_MTIME}" ]]; then
    echo "[INFO] Clone agent.py mtime updated: ${BASELINE_MTIME} -> ${NEW_MTIME} (expected)"
else
    echo "[WARN] Clone agent.py mtime unchanged (${NEW_MTIME} == ${BASELINE_MTIME}); copytree may have hit a race"
fi

exit "${RESULT}"
