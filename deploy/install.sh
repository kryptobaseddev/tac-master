#!/usr/bin/env bash
# tac-master installer for Debian 13 "Trixie" (Proxmox LXC)
#
# Run as root inside the LXC container. Idempotent: re-running is safe.
#
#   curl -fsSL https://raw.githubusercontent.com/kryptobaseddev/tac-master/main/deploy/install.sh | bash
# or
#   cd /path/to/tac-master && sudo bash deploy/install.sh

set -euo pipefail

TAC_USER="${TAC_USER:-krypto}"
TAC_HOME="${TAC_HOME:-/srv/tac-master}"
REPO_URL="${REPO_URL:-https://github.com/kryptobaseddev/tac-master.git}"
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
    sudo systemd procps \
    xdg-utils                                   `# claude code runtime dep` \
    libnss3 libatk-bridge2.0-0 libdrm2 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libasound2t64 libpango-1.0-0 libcairo2 \
    fonts-liberation                            `# Playwright Chromium runtime deps`

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

# ---- Node.js (for claude CLI + dashboard client build) ----
if ! command -v node >/dev/null 2>&1; then
    log "Installing Node.js 20.x..."
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt-get install -y nodejs
fi

# Ensure npm global bin lives under /usr/local/bin (in PATH for all users)
# and is writable by root only (no EACCES surprises on re-install).
npm config -g set prefix /usr/local

# ---- Claude Code CLI (HEADLESS MODE) ----
# The `claude` binary is invoked as a pure subprocess by the ADWs with:
#   claude -p "<prompt>" --model <m> --output-format stream-json --verbose
#         --dangerously-skip-permissions [--mcp-config .mcp.json]
# Authentication is via ANTHROPIC_API_KEY env var — NO `claude login` needed,
# NO TTY required, NO browser needed. This runs cleanly in an LXC.
log "Installing Claude Code CLI (@anthropic-ai/claude-code)..."
npm install -g @anthropic-ai/claude-code \
    || err "claude-code install failed. Run: npm install -g @anthropic-ai/claude-code"

# Clear bash's command hash so a freshly-installed binary is visible
hash -r 2>/dev/null || true

# Probe multiple locations — npm's global prefix can land in several places
# depending on Node distribution (NodeSource, nvm, distro package, etc.).
# We check the most likely candidates and symlink into /usr/local/bin if
# the binary is found in a non-standard PATH location.
log "Locating claude binary..."
NPM_PREFIX="$(npm prefix -g 2>/dev/null || echo /usr)"
NPM_ROOT="$(npm root -g 2>/dev/null || echo /usr/lib/node_modules)"
CLAUDE_BIN=""
for candidate in \
    "/usr/local/bin/claude" \
    "/usr/bin/claude" \
    "${NPM_PREFIX}/bin/claude" \
    "${NPM_ROOT}/@anthropic-ai/claude-code/bin/claude" \
    "${NPM_ROOT}/@anthropic-ai/claude-code/cli.js" \
    "$(command -v claude 2>/dev/null || true)"
do
    if [[ -n "$candidate" ]] && [[ -e "$candidate" ]]; then
        CLAUDE_BIN="$candidate"
        break
    fi
done

if [[ -z "$CLAUDE_BIN" ]]; then
    err "claude binary not found. Diagnostic:"
    log "  npm prefix -g: $NPM_PREFIX"
    log "  npm root -g:   $NPM_ROOT"
    log "  PATH:          $PATH"
    log "  Searching filesystem..."
    find /usr /opt /home -name 'claude' \( -type f -o -type l \) 2>/dev/null | head -20 | sed 's/^/    /'
    log ""
    log "  Package contents:"
    ls -la "${NPM_ROOT}/@anthropic-ai/" 2>/dev/null | sed 's/^/    /' || echo "    (not found)"
    log ""
    log "  Retry manually: npm install -g @anthropic-ai/claude-code --verbose"
    err "Cannot continue without claude binary"
fi

# Symlink into /usr/local/bin if it's somewhere else — keeps PATH simple
# for the krypto user and all future invocations
case "$CLAUDE_BIN" in
    /usr/local/bin/claude)
        ;;  # already in the canonical spot
    /usr/bin/claude)
        ;;  # standard Debian location, also fine
    *)
        log "claude found at non-standard location: $CLAUDE_BIN"
        ln -sf "$CLAUDE_BIN" /usr/local/bin/claude
        CLAUDE_BIN="/usr/local/bin/claude"
        log "Symlinked → /usr/local/bin/claude"
        ;;
esac

# Verify it's actually callable
if CLAUDE_VERSION="$("$CLAUDE_BIN" --version 2>&1)"; then
    log "✓ claude installed: $CLAUDE_VERSION  ($CLAUDE_BIN)"
else
    err "claude --version failed. Binary may be broken: $CLAUDE_BIN"
fi

# Also verify krypto can run it (after user creation below — see doctor)
# Doctor will re-verify as the service user before starting.

# ---- Playwright browsers (Chromium for the review phase) ----
# tac-master's review phase uses Playwright via MCP to take screenshots
# and verify UI changes. Install Chromium with bundled deps. This is
# idempotent — safe to re-run.
log "Installing Playwright Chromium (for review phase)..."
if ! sudo -u "$TAC_USER" bash -lc 'test -d ~/.cache/ms-playwright' >/dev/null 2>&1; then
    # Install as krypto so the browser cache lives in their home
    sudo -u "$TAC_USER" bash -lc 'npx --yes playwright@latest install chromium' \
        || log "⚠ Playwright chromium install failed (review phase will be degraded; fix manually)"
else
    log "✓ Playwright chromium cache already present"
fi

# ---- Bun (for dashboard server) ----
if ! sudo -u "$TAC_USER" bash -lc 'command -v bun' >/dev/null 2>&1; then
    log "Installing Bun for $TAC_USER..."
    sudo -u "$TAC_USER" bash -lc 'curl -fsSL https://bun.sh/install | bash'
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

# ---- Verify krypto can actually invoke claude ----
log "Verifying claude is callable by $TAC_USER..."
if sudo -u "$TAC_USER" bash -lc 'claude --version' >/dev/null 2>&1; then
    KRYPTO_CLAUDE_VER="$(sudo -u "$TAC_USER" bash -lc 'claude --version' 2>&1)"
    log "✓ $TAC_USER can run claude: $KRYPTO_CLAUDE_VER"
else
    log "⚠ $TAC_USER cannot run claude. Check PATH in ~$TAC_USER/.bashrc"
    log "   You may need: echo 'export PATH=\$PATH:/usr/local/bin' >> /home/$TAC_USER/.bashrc"
fi

# ---- Build the dashboard client (optional; warn on failure) ----
if [[ -d "$TAC_HOME/dashboard/client" ]]; then
    log "Installing dashboard client dependencies..."
    if sudo -u "$TAC_USER" bash -lc "cd $TAC_HOME/dashboard/client && npm install && npm run build"; then
        log "Dashboard client built → $TAC_HOME/dashboard/client/dist"
    else
        log "Dashboard client build failed (non-fatal; can run via 'npm run dev')"
    fi
fi

# ---- Install systemd units ----
log "Installing systemd units..."
install -m 0644 deploy/systemd/tac-master.service /etc/systemd/system/tac-master.service
install -m 0644 deploy/systemd/tac-master-webhook.service /etc/systemd/system/tac-master-webhook.service
install -m 0644 deploy/systemd/tac-master-dashboard.service /etc/systemd/system/tac-master-dashboard.service
systemctl daemon-reload
systemctl enable tac-master.service
systemctl enable tac-master-webhook.service
systemctl enable tac-master-dashboard.service

log ""
log "Installation complete."
log ""
log "Next steps:"
log "  1. Edit $TAC_HOME/config/identity.env  — add GITHUB_PAT + ANTHROPIC_API_KEY"
log "     (No 'claude login' needed — tac-master runs Claude Code fully headless"
log "      using ANTHROPIC_API_KEY from this file.)"
log "  2. Edit $TAC_HOME/config/repos.yaml    — add repos to the allowlist"
log "  3. Run doctor:    sudo -u $TAC_USER bash -lc 'cd $TAC_HOME && uv run orchestrator/daemon.py --doctor'"
log "  4. Start services:"
log "       systemctl start tac-master             # polling daemon"
log "       systemctl start tac-master-webhook     # real-time webhook listener (port 8088)"
log "       systemctl start tac-master-dashboard   # observability dashboard (port 4000 / client 5173)"
log "  5. Watch logs:    journalctl -u tac-master -f"
log "  6. Open dashboard: http://<lxc-ip>:5173 (or 4000 for API health)"
