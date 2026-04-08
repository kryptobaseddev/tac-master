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
#   CT_IP          dhcp | CIDR (e.g. 10.0.10.22/24)  (default: dhcp)
#   CT_GATEWAY     gateway (only if static)          (default: none)
#   CT_DNS         DNS server                        (default: inherited)
#   CT_TIMEZONE    timezone                          (default: host tz or UTC)
#
#   Access to the created container:
#   CT_ROOT_PASSWORD  root password inside LXC       (default: unset — pct enter only)
#   CT_SSH_ENABLE     install openssh-server         (default: unset — SSH off)
#   CT_SSH_PUBKEY     path OR contents of pubkey     (default: unset — key auth off)
#                     accepts "/root/.ssh/id_ed25519.pub" or the raw key string
#   CT_SSH_PORT       sshd listen port               (default: 22)
#
#   REPO_URL       tac-master git URL        (default: kryptobaseddev/tac-master)
#   REPO_BRANCH    git branch                (default: main)
#   TAC_WITH_CONTAINERS   also install podman mode (default: unset)
#   NO_INSTALL     skip install.sh after creation  (default: unset)
#   UNATTENDED     skip all prompts                 (default: unset)
#
# Security note on access:
#   By default no root password is set and SSH is not installed. The only
#   way in is `pct enter <CTID>` from the Proxmox host. This is the most
#   secure option. Enable CT_SSH_PUBKEY for remote access (safer than a
#   password) or CT_ROOT_PASSWORD if you must.
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

    local pve_full pve_ver
    pve_full="$(pveversion | head -1)"
    ok "Proxmox: $pve_full"
    ok "Host:    $(hostname -f 2>/dev/null || hostname)"
    ok "Kernel:  $(uname -r)"

    # Parse the pve-manager version (e.g. "pve-manager/8.1.4/ec5aff...")
    pve_ver="$(printf '%s' "$pve_full" | awk -F/ '{print $2}')"
    local pve_major pve_minor pve_patch
    IFS='.' read -r pve_major pve_minor pve_patch <<< "$pve_ver"
    pve_major="${pve_major:-0}"
    pve_minor="${pve_minor:-0}"

    # Warn if PVE is too old to recognize Debian 13.1+. PVE 8.3+ added
    # support. Older 8.x will still work via the --ostype unmanaged
    # fallback this script implements, but upgrading is cleaner.
    if [[ "$pve_major" -lt 8 ]] || { [[ "$pve_major" -eq 8 ]] && [[ "$pve_minor" -lt 3 ]]; }; then
        warn "PVE $pve_ver is older than 8.3 — Debian 13.1 template may not be in"
        warn "the hardcoded allowlist. This script will retry with --ostype unmanaged"
        warn "if needed, but upgrading is recommended:"
        warn "    apt update && apt install --only-upgrade pve-container"
        warn "  or for a full minor-version upgrade:"
        warn "    apt update && apt dist-upgrade"
    fi
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
    CT_ROOT_PASSWORD="${CT_ROOT_PASSWORD:-}"
    CT_SSH_ENABLE="${CT_SSH_ENABLE:-}"
    CT_SSH_PUBKEY="${CT_SSH_PUBKEY:-}"
    CT_SSH_PORT="${CT_SSH_PORT:-22}"
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
        --inputbox "'dhcp' or CIDR (e.g. 10.0.10.22/24)" \
        8 60 "$CT_IP" 3>&1 1>&2 2>&3) || exit 1

    if [[ "$CT_IP" != "dhcp" ]]; then
        CT_GATEWAY=$(whiptail --backtitle "tac-master" --title "Gateway" \
            --inputbox "Default gateway (leave blank to auto-derive .1 from IP)" \
            8 60 "" 3>&1 1>&2 2>&3) || exit 1
        # Auto-derive if still blank: e.g. 10.0.10.22/24 → 10.0.10.1
        if [[ -z "$CT_GATEWAY" ]]; then
            local ip_only="${CT_IP%/*}"
            CT_GATEWAY="${ip_only%.*}.1"
        fi
    fi

    REPO_URL=$(whiptail --backtitle "tac-master" --title "tac-master Repository" \
        --inputbox "Git URL to clone" 8 80 "$REPO_URL" 3>&1 1>&2 2>&3) || exit 1
    REPO_BRANCH=$(whiptail --backtitle "tac-master" --title "Branch" \
        --inputbox "Branch to check out" 8 60 "$REPO_BRANCH" 3>&1 1>&2 2>&3) || exit 1

    # Access configuration — secure-by-default, opt in to looser modes
    local access_choice
    access_choice=$(whiptail --backtitle "tac-master" --title "Access Method" \
        --menu "How should you access the LXC from outside the Proxmox host?" \
        15 70 4 \
        "pct"       "pct enter only (most secure, default)" \
        "sshkey"    "SSH + public-key auth (recommended for remote)" \
        "sshpass"   "SSH + root password (convenient, less secure)" \
        "both"      "SSH + key + root password (max convenience)" \
        3>&1 1>&2 2>&3) || exit 1

    case "$access_choice" in
        sshkey|both)
            CT_SSH_ENABLE=1
            CT_SSH_PUBKEY=$(whiptail --backtitle "tac-master" --title "SSH Public Key" \
                --inputbox "Path to public key file OR paste the key contents" \
                10 80 "${HOME}/.ssh/id_ed25519.pub" 3>&1 1>&2 2>&3) || exit 1
            ;;
    esac
    case "$access_choice" in
        sshpass|both)
            CT_SSH_ENABLE=1
            CT_ROOT_PASSWORD=$(whiptail --backtitle "tac-master" --title "Root Password" \
                --passwordbox "Set root password (8+ chars). Used for pct enter AND SSH if enabled." \
                10 70 3>&1 1>&2 2>&3) || exit 1
            [[ ${#CT_ROOT_PASSWORD} -lt 8 ]] && die "Password must be at least 8 characters."
            ;;
    esac

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

    local podman_display="no (native runtime only)"
    [[ -n "$TAC_WITH_CONTAINERS" ]] && podman_display="yes (containers opt-in)"

    local install_display="yes (runs deploy/install.sh after LXC is up)"
    [[ -n "$NO_INSTALL" ]] && install_display="SKIPPED"

    local access_display="pct enter only (most secure)"
    if [[ -n "$CT_SSH_ENABLE" ]] && [[ -n "$CT_SSH_PUBKEY" ]] && [[ -n "$CT_ROOT_PASSWORD" ]]; then
        access_display="pct enter + SSH (pubkey + root password)"
    elif [[ -n "$CT_SSH_ENABLE" ]] && [[ -n "$CT_SSH_PUBKEY" ]]; then
        access_display="pct enter + SSH (pubkey auth only)"
    elif [[ -n "$CT_SSH_ENABLE" ]] && [[ -n "$CT_ROOT_PASSWORD" ]]; then
        access_display="pct enter + SSH (root password)"
    elif [[ -n "$CT_ROOT_PASSWORD" ]]; then
        access_display="pct enter (root password set, no SSH)"
    fi

    cat <<EOF
  ${DIM}Container ID:${RESET}    ${BOLD}$CT_ID${RESET}
  ${DIM}Hostname:${RESET}        $CT_HOSTNAME
  ${DIM}Cores / RAM:${RESET}     ${CT_CORES} cores / ${CT_MEMORY} MB (+ ${CT_SWAP} MB swap)
  ${DIM}Disk:${RESET}            ${CT_DISK} GB on ${CT_STORAGE}
  ${DIM}Network:${RESET}         ${CT_IP} via ${CT_BRIDGE}${CT_GATEWAY:+ → $CT_GATEWAY}
  ${DIM}Template:${RESET}        Debian 13 "Trixie" (amd64, standard)
  ${DIM}Features:${RESET}        unprivileged · nesting=1 · keyctl=1
  ${DIM}Timezone:${RESET}        $CT_TIMEZONE
  ${DIM}Access:${RESET}          $access_display
  ${DIM}Repository:${RESET}      $REPO_URL
  ${DIM}Branch:${RESET}          $REPO_BRANCH
  ${DIM}Podman mode:${RESET}     $podman_display
  ${DIM}Auto-install:${RESET}    $install_display

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

    # Resolve the SSH pubkey early so we can inject via --ssh-public-keys
    local ssh_key_file=""
    if [[ -n "$CT_SSH_PUBKEY" ]]; then
        if [[ -f "$CT_SSH_PUBKEY" ]]; then
            ssh_key_file="$CT_SSH_PUBKEY"
        elif [[ "$CT_SSH_PUBKEY" =~ ^(ssh-|ecdsa-|sk-) ]]; then
            # Treat as raw key content — write to a temp file
            ssh_key_file="$(mktemp /tmp/tac-master-pubkey.XXXXXX)"
            printf '%s\n' "$CT_SSH_PUBKEY" > "$ssh_key_file"
            # Use an EXIT trap to clean up
            trap "rm -f '$ssh_key_file'" EXIT
        else
            warn "CT_SSH_PUBKEY is neither a file nor a recognized key format — ignoring"
        fi
    fi

    # Build the pct create argv. We'll invoke it with --ostype debian first
    # and fall back to --ostype unmanaged if Proxmox rejects the Debian
    # version (a known issue on PVE 8.x before pve-container was updated
    # to recognize Debian 13.1+).
    local -a pct_args=(
        "$CT_ID" "$TEMPLATE_REF"
        --hostname     "$CT_HOSTNAME"
        --cores        "$CT_CORES"
        --memory       "$CT_MEMORY"
        --swap         "$CT_SWAP"
        --rootfs       "${CT_STORAGE}:${CT_DISK}"
        --net0         "$net_arg"
        --features     "nesting=1,keyctl=1"
        --unprivileged 1
        --onboot       1
        --tags         "tac-master;agent;debian-13"
        --description  "tac-master autonomous TAC orchestrator. See https://github.com/kryptobaseddev/tac-master"
    )

    # Root password (omit the flag entirely if not set — no password)
    if [[ -n "$CT_ROOT_PASSWORD" ]]; then
        pct_args+=( --password "$CT_ROOT_PASSWORD" )
    fi

    # SSH pubkey injection during create (Proxmox writes it to
    # /root/.ssh/authorized_keys inside the container)
    if [[ -n "$ssh_key_file" ]]; then
        pct_args+=( --ssh-public-keys "$ssh_key_file" )
    fi

    # --- Attempt 1: --ostype debian (the preferred path) ---
    log "Attempt 1: pct create with --ostype debian"
    local create_out
    if create_out=$(pct create "${pct_args[@]}" --ostype debian 2>&1); then
        ok "LXC $CT_ID created (ostype: debian)"
        return 0
    fi

    # --- Failure handling ---
    printf '%s\n' "$create_out" | sed 's/^/    /'

    # Detect the "unsupported debian version" failure and auto-retry with
    # --ostype unmanaged. Proxmox's Debian.pm allowlist doesn't always
    # include every Debian point release; unmanaged skips the check.
    if printf '%s' "$create_out" | grep -qE "unsupported debian version"; then
        warn "Your Proxmox's pve-container doesn't recognize this Debian point release."
        warn "Retrying with --ostype unmanaged (fully supported alternative)..."

        # Clean up any partial LV left by the failed first attempt
        pct destroy "$CT_ID" --purge >/dev/null 2>&1 || true

        if pct create "${pct_args[@]}" --ostype unmanaged 2>&1 | sed 's/^/    /'; then
            ok "LXC $CT_ID created (ostype: unmanaged)"
            # Signal downstream steps that they need to do manual fixups
            export OSTYPE_UNMANAGED=1
            return 0
        fi
        die "pct create failed even with --ostype unmanaged. See errors above."
    fi

    die "pct create failed for a reason other than Debian version. See errors above."
}

# ----------------------------------------------------------------------------
# start + wait for network
# ----------------------------------------------------------------------------

start_container() {
    step "Starting LXC"
    pct start "$CT_ID" || die "pct start failed"

    # If we fell back to --ostype unmanaged, Proxmox didn't rewrite /etc/hostname
    # inside the container. Do it ourselves before anything else so hostname is
    # correct for the rest of the install.
    if [[ -n "${OSTYPE_UNMANAGED:-}" ]]; then
        log "Applying unmanaged-mode fixups (hostname, hosts)..."
        pct exec "$CT_ID" -- bash -c "echo '$CT_HOSTNAME' > /etc/hostname && hostname '$CT_HOSTNAME'" 2>/dev/null || true
        pct exec "$CT_ID" -- bash -c "grep -q '127.0.1.1' /etc/hosts || echo '127.0.1.1 $CT_HOSTNAME' >> /etc/hosts" 2>/dev/null || true
        ok "Hostname and /etc/hosts fixed"
    fi

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
# Optional: install + enable SSH inside the container
# ----------------------------------------------------------------------------

setup_ssh() {
    [[ -n "$CT_SSH_ENABLE" ]] || return 0

    step "Installing SSH server"
    pct exec "$CT_ID" -- bash -c 'DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends openssh-server' \
        >/dev/null 2>&1 \
        || die "Failed to install openssh-server"

    # If a non-default port was requested, configure sshd
    if [[ "$CT_SSH_PORT" != "22" ]]; then
        pct exec "$CT_ID" -- bash -c "sed -i 's/^#Port 22/Port $CT_SSH_PORT/' /etc/ssh/sshd_config"
    fi

    # Disable password auth if we're in pubkey-only mode
    if [[ -n "$CT_SSH_PUBKEY" ]] && [[ -z "$CT_ROOT_PASSWORD" ]]; then
        pct exec "$CT_ID" -- bash -c "sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config"
        pct exec "$CT_ID" -- bash -c "sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config"
        ok "sshd: pubkey-only auth (root password login disabled)"
    elif [[ -n "$CT_ROOT_PASSWORD" ]]; then
        pct exec "$CT_ID" -- bash -c "sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config"
        warn "sshd: root password login ENABLED (less secure, but you asked for it)"
    fi

    pct exec "$CT_ID" -- bash -c 'systemctl enable ssh && systemctl restart ssh' \
        || warn "Failed to start ssh service"
    ok "sshd running on port $CT_SSH_PORT"
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
    local access_lines=""
    access_lines+="    • ${CYAN}pct enter $CT_ID${RESET}  (from the Proxmox host, always works)"$'\n'
    if [[ -n "$CT_SSH_ENABLE" ]]; then
        if [[ -n "$CT_SSH_PUBKEY" ]] && [[ -z "$CT_ROOT_PASSWORD" ]]; then
            access_lines+="    • ${CYAN}ssh -p $CT_SSH_PORT root@$CT_ACTUAL_IP${RESET}  (pubkey auth only — password login disabled)"$'\n'
        elif [[ -n "$CT_SSH_PUBKEY" ]] && [[ -n "$CT_ROOT_PASSWORD" ]]; then
            access_lines+="    • ${CYAN}ssh -p $CT_SSH_PORT root@$CT_ACTUAL_IP${RESET}  (pubkey or root password)"$'\n'
        else
            access_lines+="    • ${CYAN}ssh -p $CT_SSH_PORT root@$CT_ACTUAL_IP${RESET}  (root password)"$'\n'
        fi
    else
        access_lines+="    • ${DIM}SSH is NOT installed. pct enter only.${RESET}"$'\n'
    fi

    cat <<EOF

${GREEN}${BOLD}╔═══════════════════════════════════════════════════════════════════╗${RESET}
${GREEN}${BOLD}║                  tac-master LXC is ready                         ║${RESET}
${GREEN}${BOLD}╚═══════════════════════════════════════════════════════════════════╝${RESET}

  ${BOLD}Container:${RESET}       $CT_ID ($CT_HOSTNAME)
  ${BOLD}IP address:${RESET}      ${CYAN}$CT_ACTUAL_IP${RESET}
  ${BOLD}Installed at:${RESET}    /srv/tac-master (inside LXC)

  ${BOLD}Access methods:${RESET}
$access_lines

  ${BOLD}Next steps${RESET} (run from the Proxmox host):

  ${MAGENTA}1.${RESET} Enter the container using any access method above, e.g.:
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
setup_ssh
install_tac_master
print_summary
