#!/usr/bin/env bash
# create-tac-master-lxc.sh
#
# Modern Proxmox VE helper script: creates a Debian 13 "Trixie" LXC
# configured for tac-master and bootstraps the autonomous orchestrator
# inside it. Runs on the Proxmox VE HOST (not inside a container).
#
# Quick install (from your Proxmox host shell, as root):
#
#   bash -c "$(wget -qLO - https://raw.githubusercontent.com/kryptobaseddev/tac-master/main/deploy/proxmox/create-tac-master-lxc.sh)"
#
# Or from a local checkout:
#
#   bash /path/to/tac-master/deploy/proxmox/create-tac-master-lxc.sh
#
# All prompts can be skipped with UNATTENDED=1 plus env overrides:
#
#   UNATTENDED=1 CT_ID=900 CT_MEMORY=8192 CT_DISK=64 bash create-tac-master-lxc.sh
#
# Env overrides (any can be set; the rest are prompted or defaulted):
#   CT_ID          container ID              (default: next free)
#   CT_HOSTNAME    hostname                  (default: tac-master)
#   CT_CORES       vCPUs                     (default: 4)
#   CT_MEMORY      RAM in MB                 (default: 4096)
#   CT_SWAP        swap in MB                (default: 2048)
#   CT_DISK        rootfs size in GB         (default: 32)
#   CT_STORAGE     storage pool              (default: local-lvm)
#   CT_BRIDGE      network bridge            (default: vmbr0)
#   CT_IP          dhcp | CIDR (e.g. 192.168.1.50/24)  (default: dhcp)
#   CT_GATEWAY     gateway (only if static)  (default: none)
#   CT_DNS         DNS server                (default: inherited)
#   CT_TIMEZONE    timezone                  (default: host tz or UTC)
#   REPO_URL       tac-master git URL        (default: krypto-agent bot fork)
#   REPO_BRANCH    git branch                (default: main)
#   TAC_WITH_CONTAINERS   also install podman mode (default: unset)
#   NO_INSTALL     skip install.sh after creation (default: unset)
#   UNATTENDED     skip all prompts                (default: unset)
#
# License: same as tac-master itself.

set -euo pipefail

# ----------------------------------------------------------------------------
# colors + logging
# ----------------------------------------------------------------------------

if [[ -t 1 ]] && [[ -z "${NO_COLOR:-}" ]]; then
    BOLD=$'\e[1m'; DIM=$'\e[2m'; RESET=$'\e[0m'
    CYAN=$'\e[1;36m'; GREEN=$'\e[1;32m'; YELLOW=$'\e[1;33m'; RED=$'\e[1;31m'
    MAGENTA=$'\e[1;35m'; BLUE=$'\e[1;34m'
else
    BOLD=""; DIM=""; RESET=""; CYAN=""; GREEN=""; YELLOW=""; RED=""; MAGENTA=""; BLUE=""
fi

banner() {
    cat <<EOF

${MAGENTA}${BOLD}  ████████╗ █████╗  ██████╗      ███╗   ███╗ █████╗ ███████╗████████╗███████╗██████╗
  ╚══██╔══╝██╔══██╗██╔════╝      ████╗ ████║██╔══██╗██╔════╝╚══██╔══╝██╔════╝██╔══██╗
     ██║   ███████║██║     █████╗██╔████╔██║███████║███████╗   ██║   █████╗  ██████╔╝
     ██║   ██╔══██║██║     ╚════╝██║╚██╔╝██║██╔══██║╚════██║   ██║   ██╔══╝  ██╔══██╗
     ██║   ██║  ██║╚██████╗      ██║ ╚═╝ ██║██║  ██║███████║   ██║   ███████╗██║  ██║
     ╚═╝   ╚═╝  ╚═╝ ╚═════╝      ╚═╝     ╚═╝╚═╝  ╚═╝╚══════╝   ╚═╝   ╚══════╝╚═╝  ╚═╝${RESET}
${DIM}          autonomous tactical agentic coding — Proxmox VE bootstrapper${RESET}

EOF
}

log()   { printf '%s»%s %s\n' "$CYAN" "$RESET" "$*"; }
ok()    { printf '%s ✓%s %s\n' "$GREEN" "$RESET" "$*"; }
warn()  { printf '%s ⚠%s %s\n' "$YELLOW" "$RESET" "$*"; }
err()   { printf '%s ✗%s %s\n' "$RED" "$RESET" "$*" >&2; }
step()  { printf '\n%s%s▎%s%s %s\n' "$BLUE" "$BOLD" "$RESET" "$BOLD" "$*"; printf '%s\n' "$RESET"; }
die()   { err "$*"; exit 1; }

# ----------------------------------------------------------------------------
# preflight
# ----------------------------------------------------------------------------

preflight() {
    step "Preflight"
    [[ $EUID -eq 0 ]] || die "Run as root on the Proxmox VE host."
    command -v pveversion >/dev/null 2>&1 || die "pveversion not found. Are you on a Proxmox VE host?"
    command -v pct        >/dev/null 2>&1 || die "pct not found — this must run on Proxmox VE."
    command -v pveam      >/dev/null 2>&1 || die "pveam not found — this must run on Proxmox VE."

    ok "Proxmox: $(pveversion | head -1)"
    ok "Host:    $(hostname -f 2>/dev/null || hostname)"
    ok "Kernel:  $(uname -r)"
}

# ----------------------------------------------------------------------------
# defaults + env override
# ----------------------------------------------------------------------------

set_defaults() {
    CT_HOSTNAME="${CT_HOSTNAME:-tac-master}"
    CT_CORES="${CT_CORES:-4}"
    CT_MEMORY="${CT_MEMORY:-4096}"
    CT_SWAP="${CT_SWAP:-2048}"
    CT_DISK="${CT_DISK:-32}"
    CT_STORAGE="${CT_STORAGE:-local-lvm}"
    CT_BRIDGE="${CT_BRIDGE:-vmbr0}"
    CT_IP="${CT_IP:-dhcp}"
    CT_GATEWAY="${CT_GATEWAY:-}"
    CT_DNS="${CT_DNS:-}"
    CT_TIMEZONE="${CT_TIMEZONE:-$(cat /etc/timezone 2>/dev/null || echo UTC)}"
    REPO_URL="${REPO_URL:-https://github.com/kryptobaseddev/tac-master.git}"
    REPO_BRANCH="${REPO_BRANCH:-main}"
    TAC_WITH_CONTAINERS="${TAC_WITH_CONTAINERS:-}"
    NO_INSTALL="${NO_INSTALL:-}"
    UNATTENDED="${UNATTENDED:-}"

    # Auto-pick next container ID
    if [[ -z "${CT_ID:-}" ]]; then
        if CT_ID="$(pvesh get /cluster/nextid 2>/dev/null)"; then
            :
        else
            CT_ID=900
        fi
    fi
}

# ----------------------------------------------------------------------------
# interactive prompts (whiptail if available)
# ----------------------------------------------------------------------------

prompt_config() {
    [[ -n "$UNATTENDED" ]] && { ok "Unattended mode — using defaults/env"; return 0; }
    if ! command -v whiptail >/dev/null 2>&1; then
        warn "whiptail not installed — using defaults/env (set UNATTENDED=1 to silence)"
        return 0
    fi

    step "Interactive configuration"

    CT_ID=$(whiptail --backtitle "tac-master" --title "Container ID" \
        --inputbox "LXC container ID" 8 60 "$CT_ID" 3>&1 1>&2 2>&3) || exit 1
    CT_HOSTNAME=$(whiptail --backtitle "tac-master" --title "Hostname" \
        --inputbox "Hostname for the LXC" 8 60 "$CT_HOSTNAME" 3>&1 1>&2 2>&3) || exit 1
    CT_CORES=$(whiptail --backtitle "tac-master" --title "Cores" \
        --inputbox "vCPU cores (4 recommended for up to 5 concurrent ADWs)" \
        8 60 "$CT_CORES" 3>&1 1>&2 2>&3) || exit 1
    CT_MEMORY=$(whiptail --backtitle "tac-master" --title "Memory" \
        --inputbox "RAM (MB) — 4096 minimum, 8192 if using podman mode" \
        8 60 "$CT_MEMORY" 3>&1 1>&2 2>&3) || exit 1
    CT_SWAP=$(whiptail --backtitle "tac-master" --title "Swap" \
        --inputbox "Swap (MB)" 8 60 "$CT_SWAP" 3>&1 1>&2 2>&3) || exit 1
    CT_DISK=$(whiptail --backtitle "tac-master" --title "Disk" \
        --inputbox "Rootfs size (GB) — 32 minimum, 64 if using podman mode" \
        8 60 "$CT_DISK" 3>&1 1>&2 2>&3) || exit 1
    CT_STORAGE=$(whiptail --backtitle "tac-master" --title "Storage" \
        --inputbox "Proxmox storage pool" 8 60 "$CT_STORAGE" 3>&1 1>&2 2>&3) || exit 1
    CT_BRIDGE=$(whiptail --backtitle "tac-master" --title "Network Bridge" \
        --inputbox "Linux bridge for the LXC" 8 60 "$CT_BRIDGE" 3>&1 1>&2 2>&3) || exit 1
    CT_IP=$(whiptail --backtitle "tac-master" --title "IP Address" \
        --inputbox "'dhcp' or CIDR (e.g. 192.168.1.50/24)" \
        8 60 "$CT_IP" 3>&1 1>&2 2>&3) || exit 1

    if [[ "$CT_IP" != "dhcp" ]]; then
        CT_GATEWAY=$(whiptail --backtitle "tac-master" --title "Gateway" \
            --inputbox "Default gateway for static IP" \
            8 60 "${CT_GATEWAY:-192.168.1.1}" 3>&1 1>&2 2>&3) || exit 1
    fi

    REPO_URL=$(whiptail --backtitle "tac-master" --title "tac-master Repository" \
        --inputbox "Git URL to clone" 8 80 "$REPO_URL" 3>&1 1>&2 2>&3) || exit 1
    REPO_BRANCH=$(whiptail --backtitle "tac-master" --title "Branch" \
        --inputbox "Branch to check out" 8 60 "$REPO_BRANCH" 3>&1 1>&2 2>&3) || exit 1

    if whiptail --backtitle "tac-master" --title "Podman Runtime" \
        --yesno "Also install optional Podman runtime mode?\n\n(Required if any allowlisted repo uses runtime: podman in its config. Adds ~5 min to install and ~2 GB to the image.)" \
        11 70; then
        TAC_WITH_CONTAINERS=1
    fi
}

# ----------------------------------------------------------------------------
# show the plan + confirm
# ----------------------------------------------------------------------------

show_config() {
    step "Configuration summary"
    cat <<EOF
  ${DIM}Container ID:${RESET}    ${BOLD}$CT_ID${RESET}
  ${DIM}Hostname:${RESET}        $CT_HOSTNAME
  ${DIM}Cores / RAM:${RESET}     ${CT_CORES} cores / ${CT_MEMORY} MB (+ ${CT_SWAP} MB swap)
  ${DIM}Disk:${RESET}            ${CT_DISK} GB on ${CT_STORAGE}
  ${DIM}Network:${RESET}         ${CT_IP} via ${CT_BRIDGE}${CT_GATEWAY:+ → $CT_GATEWAY}
  ${DIM}Template:${RESET}        Debian 13 "Trixie" (amd64, standard)
  ${DIM}Features:${RESET}        unprivileged · nesting=1 · keyctl=1
  ${DIM}Timezone:${RESET}        $CT_TIMEZONE
  ${DIM}Repository:${RESET}      $REPO_URL
  ${DIM}Branch:${RESET}          $REPO_BRANCH
  ${DIM}Podman mode:${RESET}     ${TAC_WITH_CONTAINERS:+yes (containers opt-in)}${TAC_WITH_CONTAINERS:-no (native runtime only)}
  ${DIM}Auto-install:${RESET}    ${NO_INSTALL:+SKIPPED}${NO_INSTALL:-yes (runs deploy/install.sh after LXC is up)}

EOF
}

confirm() {
    [[ -n "$UNATTENDED" ]] && return 0
    printf '%sProceed with LXC creation? [y/N]%s ' "$BOLD" "$RESET"
    read -r ans
    [[ "$ans" =~ ^[Yy]$ ]] || die "Aborted by user."
}

# ----------------------------------------------------------------------------
# template discovery + download
# ----------------------------------------------------------------------------

ensure_template() {
    step "Debian 13 template"

    pveam update >/dev/null 2>&1 || warn "pveam update failed — using cached catalog"

    # Find the latest Debian 13 standard template
    local template
    template=$(pveam available --section system 2>/dev/null \
        | awk '/debian-13.*amd64/ {print $2}' \
        | sort -V | tail -1)

    if [[ -z "$template" ]]; then
        die "No Debian 13 template in pveam catalog. Try: pveam update"
    fi

    log "Template: $template"

    if pveam list local 2>/dev/null | awk '{print $1}' | grep -q "local:vztmpl/$template"; then
        ok "Already downloaded"
    else
        log "Downloading (~100 MB)..."
        pveam download local "$template" || die "pveam download failed"
        ok "Downloaded"
    fi

    TEMPLATE_REF="local:vztmpl/$template"
}

# ----------------------------------------------------------------------------
# LXC creation
# ----------------------------------------------------------------------------

create_container() {
    step "Creating LXC $CT_ID"

    if pct status "$CT_ID" >/dev/null 2>&1; then
        die "LXC $CT_ID already exists. Destroy it first (pct stop $CT_ID && pct destroy $CT_ID) or choose a different CT_ID."
    fi

    local net_arg="name=eth0,bridge=${CT_BRIDGE}"
    if [[ "$CT_IP" == "dhcp" ]]; then
        net_arg="${net_arg},ip=dhcp"
    else
        net_arg="${net_arg},ip=${CT_IP}"
        [[ -n "$CT_GATEWAY" ]] && net_arg="${net_arg},gw=${CT_GATEWAY}"
    fi

    # Note: we omit --password so the container has no root password.
    # Access is via `pct enter` from the Proxmox host only.
    pct create "$CT_ID" "$TEMPLATE_REF" \
        --hostname   "$CT_HOSTNAME" \
        --cores      "$CT_CORES" \
        --memory     "$CT_MEMORY" \
        --swap       "$CT_SWAP" \
        --rootfs     "${CT_STORAGE}:${CT_DISK}" \
        --net0       "$net_arg" \
        --features   nesting=1,keyctl=1 \
        --unprivileged 1 \
        --onboot     1 \
        --ostype     debian \
        --tags       "tac-master;agent;debian-13" \
        --description "tac-master autonomous TAC orchestrator. See https://github.com/kryptobaseddev/tac-master" \
        || die "pct create failed"

    ok "LXC $CT_ID created"
}

# ----------------------------------------------------------------------------
# start + wait for network
# ----------------------------------------------------------------------------

start_container() {
    step "Starting LXC"
    pct start "$CT_ID" || die "pct start failed"

    log "Waiting for network..."
    local i
    for i in {1..60}; do
        if pct exec "$CT_ID" -- bash -c 'ip -4 addr show eth0 | grep -q "inet "' 2>/dev/null; then
            break
        fi
        sleep 1
    done

    CT_ACTUAL_IP=$(pct exec "$CT_ID" -- bash -c "ip -4 addr show eth0 | awk '/inet /{print \$2}' | cut -d/ -f1" 2>/dev/null || echo unknown)
    if [[ "$CT_ACTUAL_IP" == "unknown" || -z "$CT_ACTUAL_IP" ]]; then
        die "LXC failed to obtain an IP. Check bridge ($CT_BRIDGE) and DHCP."
    fi
    ok "Network up: $CT_ACTUAL_IP"

    # Set timezone
    log "Setting timezone to $CT_TIMEZONE..."
    pct exec "$CT_ID" -- bash -c "ln -sf /usr/share/zoneinfo/$CT_TIMEZONE /etc/localtime && echo $CT_TIMEZONE > /etc/timezone" 2>/dev/null || \
        warn "Failed to set timezone (non-fatal)"

    log "Waiting for apt to be ready..."
    for i in {1..30}; do
        if pct exec "$CT_ID" -- bash -c 'apt-get update -qq' >/dev/null 2>&1; then
            break
        fi
        sleep 2
    done
    ok "Package manager ready"
}

# ----------------------------------------------------------------------------
# install tac-master inside the LXC
# ----------------------------------------------------------------------------

install_tac_master() {
    if [[ -n "$NO_INSTALL" ]]; then
        warn "NO_INSTALL set — skipping tac-master install. LXC is ready but empty."
        return 0
    fi

    step "Installing tac-master inside LXC"

    log "Installing bootstrap tools (curl, git, ca-certificates)..."
    pct exec "$CT_ID" -- bash -c 'DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends curl git ca-certificates' \
        >/dev/null 2>&1 \
        || die "Failed to install bootstrap tools"

    log "Cloning $REPO_URL (branch: $REPO_BRANCH)..."
    pct exec "$CT_ID" -- bash -c "git clone --depth 1 --branch '$REPO_BRANCH' '$REPO_URL' /tmp/tac-master" \
        || die "git clone failed — is the repo public and the URL correct?"

    log "Running deploy/install.sh (this takes 3-5 min)..."
    if ! pct exec "$CT_ID" -- bash -c 'cd /tmp/tac-master && REPO_URL="'"$REPO_URL"'" BRANCH="'"$REPO_BRANCH"'" bash deploy/install.sh' 2>&1 | sed 's/^/    /'; then
        die "deploy/install.sh failed. Check logs inside the LXC with: pct enter $CT_ID"
    fi
    ok "tac-master installed at /srv/tac-master"

    if [[ -n "$TAC_WITH_CONTAINERS" ]]; then
        log "Installing optional Podman runtime (takes ~5 min)..."
        if pct exec "$CT_ID" -- bash -c 'cd /srv/tac-master && bash scripts/tac-update.sh install-podman' 2>&1 | sed 's/^/    /'; then
            ok "Podman runtime ready"
        else
            warn "Podman install failed — you can re-run later with 'sudo bash scripts/tac-update.sh install-podman'"
        fi
    fi
}

# ----------------------------------------------------------------------------
# final summary
# ----------------------------------------------------------------------------

print_summary() {
    cat <<EOF

${GREEN}${BOLD}╔═══════════════════════════════════════════════════════════════════╗${RESET}
${GREEN}${BOLD}║                  tac-master LXC is ready                         ║${RESET}
${GREEN}${BOLD}╚═══════════════════════════════════════════════════════════════════╝${RESET}

  ${BOLD}Container:${RESET}       $CT_ID ($CT_HOSTNAME)
  ${BOLD}IP address:${RESET}      ${CYAN}$CT_ACTUAL_IP${RESET}
  ${BOLD}Shell access:${RESET}    pct enter $CT_ID
  ${BOLD}Installed at:${RESET}    /srv/tac-master (inside LXC)

  ${BOLD}Next steps${RESET} (run from the Proxmox host):

  ${MAGENTA}1.${RESET} Enter the container:
       ${CYAN}pct enter $CT_ID${RESET}

  ${MAGENTA}2.${RESET} Fill in your secrets:
       ${CYAN}nano /srv/tac-master/config/identity.env${RESET}
       Required: ${BOLD}GITHUB_PAT${RESET} and ${BOLD}ANTHROPIC_API_KEY${RESET}

  ${MAGENTA}3.${RESET} Add repositories to the allowlist:
       ${CYAN}nano /srv/tac-master/config/repos.yaml${RESET}

  ${MAGENTA}4.${RESET} Verify the stack is healthy:
       ${CYAN}sudo -u krypto bash -lc 'cd /srv/tac-master && uv run orchestrator/daemon.py --doctor'${RESET}

  ${MAGENTA}5.${RESET} Start the three services:
       ${CYAN}systemctl start tac-master tac-master-webhook tac-master-dashboard${RESET}

  ${MAGENTA}6.${RESET} Watch it work:
       ${CYAN}journalctl -u tac-master -f${RESET}

  ${BOLD}Dashboard URLs${RESET} (from any host on your network):
    API   ${CYAN}http://${CT_ACTUAL_IP}:4000/health${RESET}
    WS    ${CYAN}ws://${CT_ACTUAL_IP}:4000/stream${RESET}
    UI    ${CYAN}http://${CT_ACTUAL_IP}:5173${RESET}  (run ${DIM}cd dashboard/client && npm run dev${RESET})
    Webhook  ${CYAN}http://${CT_ACTUAL_IP}:8088/webhook/github${RESET}

  ${BOLD}Ongoing maintenance:${RESET}
    ${CYAN}sudo bash /srv/tac-master/scripts/tac-update.sh check${RESET}     # version report
    ${CYAN}sudo bash /srv/tac-master/scripts/tac-update.sh update${RESET}    # refresh everything

  ${DIM}Full docs: https://github.com/kryptobaseddev/tac-master${RESET}

EOF
}

# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------

banner
preflight
set_defaults
prompt_config
show_config
confirm
ensure_template
create_container
start_container
install_tac_master
print_summary
