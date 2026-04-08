#!/usr/bin/env bash
# tac-master installer for Debian 13 "Trixie" (Proxmox LXC)
#
# Run as root inside the LXC container. Idempotent: re-running is safe.
#
#   curl -fsSL https://raw.githubusercontent.com/OWNER/tac-master/main/deploy/install.sh | bash
# or
#   cd /path/to/tac-master && sudo bash deploy/install.sh

set -euo pipefail

TAC_USER="${TAC_USER:-krypto}"
TAC_HOME="${TAC_HOME:-/srv/tac-master}"
REPO_URL="${REPO_URL:-https://github.com/OWNER/tac-master.git}"
BRANCH="${BRANCH:-main}"

log() { printf '\033[1;34m[tac-master]\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || err "Run as root (sudo bash deploy/install.sh)"

log "Detecting Debian version..."
. /etc/os-release
[[ "${VERSION_CODENAME:-}" == "trixie" ]] || log "Warning: not Debian trixie (got ${VERSION_CODENAME:-unknown}), continuing anyway."

log "Installing OS packages..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends \
    ca-certificates curl git jq sqlite3 build-essential \
    python3 python3-venv python3-pip \
    sudo systemd procps

# ---- GitHub CLI (gh) ----
if ! command -v gh >/dev/null 2>&1; then
    log "Installing GitHub CLI..."
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | tee /etc/apt/keyrings/githubcli-archive-keyring.gpg > /dev/null
    chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list
    apt-get update -qq
    apt-get install -y gh
fi

# ---- Node.js (for claude CLI + dashboard) ----
if ! command -v node >/dev/null 2>&1; then
    log "Installing Node.js 20.x..."
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt-get install -y nodejs
fi

# ---- Claude Code CLI ----
if ! command -v claude >/dev/null 2>&1; then
    log "Installing Claude Code CLI..."
    npm install -g @anthropic-ai/claude-code || log "claude-code install failed; install manually later"
fi

# ---- Create service user ----
if ! id "$TAC_USER" >/dev/null 2>&1; then
    log "Creating user $TAC_USER..."
    useradd --system --create-home --shell /bin/bash "$TAC_USER"
fi

# ---- Install uv for the service user ----
if ! sudo -u "$TAC_USER" bash -lc 'command -v uv' >/dev/null 2>&1; then
    log "Installing uv for $TAC_USER..."
    sudo -u "$TAC_USER" bash -lc 'curl -LsSf https://astral.sh/uv/install.sh | sh'
fi

# ---- Clone or update tac-master ----
if [[ ! -d "$TAC_HOME/.git" ]]; then
    log "Cloning tac-master into $TAC_HOME..."
    mkdir -p "$TAC_HOME"
    chown "$TAC_USER:$TAC_USER" "$TAC_HOME"
    sudo -u "$TAC_USER" git clone --branch "$BRANCH" "$REPO_URL" "$TAC_HOME"
else
    log "Updating existing $TAC_HOME..."
    sudo -u "$TAC_USER" git -C "$TAC_HOME" fetch --all
    sudo -u "$TAC_USER" git -C "$TAC_HOME" reset --hard "origin/$BRANCH"
fi

# ---- Prepare config ----
log "Preparing config directory..."
cd "$TAC_HOME"
for f in repos.yaml budgets.yaml policies.yaml; do
    if [[ ! -f "config/$f" ]]; then
        cp "config/$f.sample" "config/$f"
        log "Created config/$f (review and edit before starting)"
    fi
done
if [[ ! -f config/identity.env ]]; then
    cp config/identity.env.sample config/identity.env
    chmod 600 config/identity.env
    chown "$TAC_USER:$TAC_USER" config/identity.env
    log "Created config/identity.env (fill in GITHUB_PAT and ANTHROPIC_API_KEY)"
fi
chown -R "$TAC_USER:$TAC_USER" "$TAC_HOME"

# ---- Install systemd unit ----
log "Installing systemd unit..."
install -m 0644 deploy/systemd/tac-master.service /etc/systemd/system/tac-master.service
systemctl daemon-reload
systemctl enable tac-master.service

log ""
log "Installation complete."
log ""
log "Next steps:"
log "  1. Edit $TAC_HOME/config/identity.env  — add GITHUB_PAT + ANTHROPIC_API_KEY"
log "  2. Edit $TAC_HOME/config/repos.yaml    — add repos to the allowlist"
log "  3. Run doctor:    sudo -u $TAC_USER bash -lc 'cd $TAC_HOME && uv run orchestrator/daemon.py --doctor'"
log "  4. Start service: systemctl start tac-master"
log "  5. Watch logs:    journalctl -u tac-master -f"
