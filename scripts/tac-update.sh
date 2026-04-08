#!/usr/bin/env bash
# tac-update.sh — all-in-one install/update/check for tac-master dependencies
#
# Runs as root (via sudo). Updates:
#   - apt packages
#   - Node.js (via NodeSource repo)
#   - Claude Code CLI (npm global)
#   - uv (service user)
#   - Bun (service user)
#   - Podman (if installed — optional runtime mode)
#   - Playwright Chromium (for the review phase)
#   - tac-master itself (git pull on /srv/tac-master)
#   - Dashboard client (rebuilds dist/)
#
# Then gracefully restarts the 3 systemd units and runs the doctor.
#
# Usage:
#   sudo bash scripts/tac-update.sh check                  # report versions only
#   sudo bash scripts/tac-update.sh update                 # update everything + restart
#   sudo bash scripts/tac-update.sh update --dry-run       # show what would update
#   sudo bash scripts/tac-update.sh update --system-only   # skip tac-master git pull
#   sudo bash scripts/tac-update.sh update --no-restart    # skip service restart
#   sudo bash scripts/tac-update.sh install-podman         # install optional Podman mode
#
# Env overrides:
#   TAC_USER=krypto        service user
#   TAC_HOME=/srv/tac-master  install location

set -euo pipefail

TAC_USER="${TAC_USER:-krypto}"
TAC_HOME="${TAC_HOME:-/srv/tac-master}"
SERVICES=(tac-master tac-master-webhook tac-master-dashboard)

# -------- colors / logging ----------------------------------------------------

if [[ -t 1 ]]; then
    C_CYAN=$'\033[1;36m'; C_GREEN=$'\033[1;32m'; C_YELLOW=$'\033[1;33m'
    C_RED=$'\033[1;31m'; C_DIM=$'\033[2m'; C_RESET=$'\033[0m'
else
    C_CYAN=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_DIM=""; C_RESET=""
fi

log()  { printf '%s[tac-update]%s %s\n' "$C_CYAN" "$C_RESET" "$*"; }
ok()   { printf '%s  ✓%s %s\n' "$C_GREEN" "$C_RESET" "$*"; }
warn() { printf '%s  ⚠%s %s\n' "$C_YELLOW" "$C_RESET" "$*"; }
err()  { printf '%s  ✗%s %s\n' "$C_RED" "$C_RESET" "$*" >&2; }
dim()  { printf '%s  %s%s\n' "$C_DIM" "$*" "$C_RESET"; }

die() { err "$*"; exit 1; }

require_root() {
    [[ $EUID -eq 0 ]] || die "Run as root (sudo bash scripts/tac-update.sh $*)"
}

run_as_user() {
    # Run a command as the service user with their shell env loaded
    sudo -u "$TAC_USER" bash -lc "$*"
}

# -------- check functions -----------------------------------------------------
# Each returns the installed version on stdout; exit 0 means present.

check_apt_upgradable() {
    apt-get update -qq 2>/dev/null || return 1
    local n
    n=$(apt list --upgradable 2>/dev/null | tail -n +2 | grep -c '' || true)
    echo "$n package(s) upgradable"
}

check_node() {
    command -v node >/dev/null || { echo "not installed"; return 1; }
    node --version
}

check_npm() {
    command -v npm >/dev/null || { echo "not installed"; return 1; }
    npm --version
}

check_claude() {
    command -v claude >/dev/null || { echo "not installed"; return 1; }
    claude --version 2>&1
}

check_uv() {
    if ! run_as_user 'command -v uv' >/dev/null 2>&1; then
        echo "not installed for $TAC_USER"; return 1
    fi
    run_as_user 'uv --version'
}

check_bun() {
    if ! run_as_user 'command -v bun' >/dev/null 2>&1; then
        echo "not installed for $TAC_USER"; return 1
    fi
    run_as_user 'bun --version'
}

check_podman() {
    command -v podman >/dev/null || { echo "not installed (optional)"; return 1; }
    podman --version
}

check_playwright() {
    local cache
    cache=$(run_as_user 'ls -d ~/.cache/ms-playwright/chromium-* 2>/dev/null | head -1' || true)
    if [[ -z "$cache" ]]; then
        echo "no chromium cache"; return 1
    fi
    basename "$cache"
}

check_gh() {
    command -v gh >/dev/null || { echo "not installed"; return 1; }
    gh --version | head -1
}

check_tac_master() {
    [[ -d "$TAC_HOME/.git" ]] || { echo "not cloned"; return 1; }
    local cur ahead
    cur=$(run_as_user "cd $TAC_HOME && git rev-parse --short HEAD")
    run_as_user "cd $TAC_HOME && git fetch -q 2>/dev/null" || warn "fetch failed"
    ahead=$(run_as_user "cd $TAC_HOME && git rev-list --count HEAD..@{u} 2>/dev/null || echo 0")
    if [[ "$ahead" != "0" ]]; then
        echo "$cur ($ahead commits behind)"
    else
        echo "$cur (up to date)"
    fi
}

check_services() {
    for svc in "${SERVICES[@]}"; do
        local state
        state=$(systemctl is-active "$svc" 2>&1 || true)
        printf '  %-30s %s\n' "$svc" "$state"
    done
}

# -------- update functions ----------------------------------------------------

update_apt() {
    log "Updating apt packages..."
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get upgrade -y --no-install-recommends
    apt-get autoremove -y
    ok "apt updated"
}

update_node() {
    log "Updating Node.js (via NodeSource)..."
    if command -v node >/dev/null; then
        # Re-run the nodesource setup to ensure the right major
        curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >/dev/null
    fi
    apt-get install -y nodejs
    ok "node: $(node --version)"
}

update_claude() {
    log "Updating Claude Code CLI..."
    # The package name is stable; --global bin lives in /usr/local/bin due to
    # the prefix set in install.sh
    npm install -g @anthropic-ai/claude-code@latest
    local v; v=$(claude --version 2>&1)
    ok "claude: $v"
}

update_uv() {
    log "Updating uv (as $TAC_USER)..."
    if run_as_user 'uv self update' 2>&1; then
        ok "uv: $(run_as_user 'uv --version')"
    else
        warn "uv self update failed; re-running installer"
        run_as_user 'curl -LsSf https://astral.sh/uv/install.sh | sh'
    fi
}

update_bun() {
    log "Updating Bun (as $TAC_USER)..."
    if run_as_user 'bun upgrade' 2>&1 | tail -5; then
        ok "bun: $(run_as_user 'bun --version')"
    else
        warn "bun upgrade failed"
    fi
}

update_podman() {
    if ! command -v podman >/dev/null; then
        dim "podman not installed — skipping (use install-podman to enable)"
        return 0
    fi
    log "Updating Podman..."
    apt-get install -y podman fuse-overlayfs slirp4netns uidmap
    ok "podman: $(podman --version)"
}

update_playwright() {
    log "Refreshing Playwright Chromium (as $TAC_USER)..."
    if run_as_user 'npx --yes playwright@latest install chromium' >/dev/null 2>&1; then
        ok "playwright chromium installed"
    else
        warn "playwright install failed — review phase may be degraded"
    fi
}

update_gh() {
    log "Updating GitHub CLI..."
    apt-get install -y gh
    ok "gh: $(gh --version | head -1)"
}

update_tac_master() {
    [[ -d "$TAC_HOME/.git" ]] || die "tac-master not cloned at $TAC_HOME"
    log "Pulling tac-master from origin..."
    run_as_user "cd $TAC_HOME && git fetch --all --prune"
    local branch
    branch=$(run_as_user "cd $TAC_HOME && git rev-parse --abbrev-ref HEAD")
    local ahead
    ahead=$(run_as_user "cd $TAC_HOME && git rev-list --count HEAD..origin/$branch 2>/dev/null || echo 0")
    if [[ "$ahead" == "0" ]]; then
        ok "tac-master already up to date ($branch)"
    else
        run_as_user "cd $TAC_HOME && git reset --hard origin/$branch"
        ok "tac-master updated to $(run_as_user "cd $TAC_HOME && git rev-parse --short HEAD")"
    fi
}

update_dashboard_client() {
    [[ -d "$TAC_HOME/dashboard/client" ]] || return 0
    log "Rebuilding dashboard client..."
    if run_as_user "cd $TAC_HOME/dashboard/client && npm install --silent && npm run build" >/dev/null 2>&1; then
        ok "dashboard client rebuilt"
    else
        warn "dashboard client build failed (non-fatal; try 'npm run dev' manually)"
    fi
}

# -------- service management --------------------------------------------------

stop_services() {
    log "Stopping services (allowing in-flight runs to finish)..."
    for svc in "${SERVICES[@]}"; do
        if systemctl is-active "$svc" >/dev/null 2>&1; then
            systemctl stop "$svc" && ok "$svc stopped" || warn "$svc stop failed"
        fi
    done
}

start_services() {
    log "Starting services..."
    systemctl daemon-reload
    for svc in "${SERVICES[@]}"; do
        if systemctl is-enabled "$svc" >/dev/null 2>&1; then
            systemctl start "$svc" && ok "$svc started" || err "$svc failed to start"
        fi
    done
}

restart_services() {
    stop_services
    start_services
}

wait_for_healthy() {
    local svc=$1
    local deadline=$(( $(date +%s) + 30 ))
    while (( $(date +%s) < deadline )); do
        if systemctl is-active "$svc" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 1
}

run_doctor() {
    log "Running doctor..."
    if run_as_user "cd $TAC_HOME && uv run orchestrator/daemon.py --doctor"; then
        ok "doctor passed"
    else
        warn "doctor reported issues — see above"
    fi
}

# -------- podman install (optional subcommand) --------------------------------

install_podman_subcommand() {
    log "Installing optional Podman runtime mode..."
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y podman fuse-overlayfs slirp4netns uidmap \
        buildah skopeo passt

    # Configure subuid/subgid for rootless mode
    local have_subuid
    have_subuid=$(grep -c "^${TAC_USER}:" /etc/subuid 2>/dev/null || echo 0)
    if [[ "$have_subuid" == "0" ]]; then
        log "Configuring subuid/subgid ranges for $TAC_USER..."
        usermod --add-subuids 100000-165535 --add-subgids 100000-165535 "$TAC_USER"
    fi

    # Initial rootless setup as the service user
    run_as_user 'podman system migrate' >/dev/null 2>&1 || true
    run_as_user 'podman info' >/dev/null 2>&1 \
        && ok "podman rootless mode functional for $TAC_USER" \
        || warn "podman rootless init failed; check user namespaces"

    # Build the tac-worker base image
    if [[ -f "$TAC_HOME/deploy/docker/Dockerfile.tac-worker" ]]; then
        log "Building tac-worker base image (may take ~5 min)..."
        if run_as_user "cd $TAC_HOME && podman build -t tac-worker:latest -f deploy/docker/Dockerfile.tac-worker ."; then
            ok "tac-worker:latest built"
        else
            err "image build failed — container mode will not work"
        fi
    else
        warn "Dockerfile not found at deploy/docker/Dockerfile.tac-worker"
    fi

    log ""
    log "To enable container mode for a repo, set in config/repos.yaml:"
    log "    runtime: podman"
    log "    container_image: tac-worker:latest   # or a per-repo image"
}

# -------- subcommand: check ---------------------------------------------------

cmd_check() {
    log "tac-master dependency check"
    printf '\n'
    printf '  %-15s %s\n' "apt:"        "$(check_apt_upgradable || echo error)"
    printf '  %-15s %s\n' "node:"       "$(check_node || true)"
    printf '  %-15s %s\n' "npm:"        "$(check_npm || true)"
    printf '  %-15s %s\n' "claude code:" "$(check_claude || true)"
    printf '  %-15s %s\n' "uv:"         "$(check_uv || true)"
    printf '  %-15s %s\n' "bun:"        "$(check_bun || true)"
    printf '  %-15s %s\n' "podman:"     "$(check_podman || true)"
    printf '  %-15s %s\n' "playwright:" "$(check_playwright || true)"
    printf '  %-15s %s\n' "gh:"         "$(check_gh || true)"
    printf '  %-15s %s\n' "tac-master:" "$(check_tac_master || true)"
    printf '\n  %s\n' "systemd services:"
    check_services
    printf '\n'
}

# -------- subcommand: update --------------------------------------------------

cmd_update() {
    local dry_run=0 system_only=0 no_restart=0
    for arg in "$@"; do
        case "$arg" in
            --dry-run)      dry_run=1 ;;
            --system-only)  system_only=1 ;;
            --no-restart)   no_restart=1 ;;
            *) die "Unknown flag: $arg" ;;
        esac
    done

    if [[ $dry_run -eq 1 ]]; then
        log "DRY RUN — no changes will be made"
        cmd_check
        log "Would update: apt, node, claude, uv, bun, playwright, gh"
        [[ $system_only -eq 0 ]] && log "Would also: git pull tac-master + rebuild dashboard"
        return 0
    fi

    log "Updating tac-master stack"
    log ""

    update_apt
    update_node
    update_gh
    update_claude
    update_uv
    update_bun
    update_podman
    update_playwright

    if [[ $system_only -eq 0 ]]; then
        update_tac_master
        update_dashboard_client
    else
        dim "Skipping tac-master self-update (--system-only)"
    fi

    if [[ $no_restart -eq 0 ]]; then
        restart_services
        log ""
        run_doctor
    else
        dim "Skipping service restart (--no-restart)"
        log "Remember to: sudo systemctl restart ${SERVICES[*]}"
    fi

    log ""
    log "Update complete."
}

# -------- main ---------------------------------------------------------------

cmd="${1:-}"
case "$cmd" in
    check)
        cmd_check
        ;;
    update)
        require_root update "${@:2}"
        cmd_update "${@:2}"
        ;;
    install-podman)
        require_root install-podman
        install_podman_subcommand
        ;;
    ""|-h|--help|help)
        cat <<'EOF'
tac-update.sh — manage tac-master's dependency stack

Subcommands:
  check                        Print versions of all deps, flag stale (read-only)
  update                       Update apt, node, claude, uv, bun, playwright,
                               gh, podman (if installed), tac-master itself,
                               then restart services and run doctor
  update --dry-run             Preview what update would do
  update --system-only         Skip tac-master git pull (only update deps)
  update --no-restart          Update everything but don't restart services
  install-podman               Install optional Podman rootless runtime mode
                               and build the tac-worker base image

Examples:
  sudo bash scripts/tac-update.sh check
  sudo bash scripts/tac-update.sh update
  sudo bash scripts/tac-update.sh update --dry-run
  sudo bash scripts/tac-update.sh install-podman

Environment:
  TAC_USER        (default: krypto)
  TAC_HOME        (default: /srv/tac-master)
EOF
        ;;
    *)
        die "Unknown subcommand: $cmd (try: check, update, install-podman, help)"
        ;;
esac
