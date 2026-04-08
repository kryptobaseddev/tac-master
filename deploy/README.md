# Deploying tac-master to a Proxmox LXC (Debian 13 "Trixie")

This guide sets up `tac-master` as a long-running systemd service in a
lightweight LXC container on Proxmox.

## 1. Create the LXC container

On the Proxmox host (shell or web UI):

```bash
# Download Debian 13 template if not already cached
pveam update
pveam available | grep trixie
pveam download local debian-13-standard_13.0-1_amd64.tar.zst

# Create the container (adjust IDs, storage, network to match your env)
pct create 900 \
    local:vztmpl/debian-13-standard_13.0-1_amd64.tar.zst \
    --hostname tac-master \
    --cores 4 --memory 4096 --swap 2048 \
    --rootfs local-lvm:32 \
    --net0 name=eth0,bridge=vmbr0,ip=dhcp \
    --features nesting=1 \
    --unprivileged 1 \
    --onboot 1 \
    --password

pct start 900
pct enter 900
```

**Why `nesting=1`?** The ADWs shell out to `git worktree add` and occasionally
to Playwright for E2E tests. Nesting lets container-internal subprocesses
behave normally.

**Resources**: 4 vCPU / 4 GB / 32 GB disk is a reasonable starting point for
up to ~5 concurrent runs. Scale up if you raise `max_concurrent_runs` above
that.

## 2. Run the installer

Inside the container:

```bash
apt-get update && apt-get install -y curl git

# Option A: one-liner (after pushing this repo to GitHub)
curl -fsSL https://raw.githubusercontent.com/OWNER/tac-master/main/deploy/install.sh \
    | REPO_URL=https://github.com/OWNER/tac-master.git bash

# Option B: clone and run locally
git clone https://github.com/OWNER/tac-master.git /tmp/tac-master
bash /tmp/tac-master/deploy/install.sh
```

The installer:
- Installs `git`, `gh`, `node`, `sqlite3`, build tools
- Installs Claude Code CLI via npm
- Creates the `krypto` system user
- Installs `uv` as `krypto`
- Clones the repo into `/srv/tac-master`
- Copies `.sample` config files into place
- Installs + enables the `tac-master.service` systemd unit
- **Does NOT start the service** — you must configure secrets first

## 3. Configure secrets

```bash
nano /srv/tac-master/config/identity.env
```

Fill in at minimum:

```
GITHUB_USER=krypto-agent
GITHUB_PAT=ghp_...         # fine-grained PAT from krypto-agent account
ANTHROPIC_API_KEY=sk-ant-...
```

Restrict permissions:

```bash
chmod 600 /srv/tac-master/config/identity.env
chown krypto:krypto /srv/tac-master/config/identity.env
```

## 4. Configure the allowlist

```bash
nano /srv/tac-master/config/repos.yaml
```

Add every repo that `krypto-agent` has been granted collaborator access to.
At minimum include tac-master's own repo for the self-improvement loop.

## 5. Run the doctor

Validates config, GitHub connectivity, substrate presence:

```bash
sudo -u krypto bash -lc 'cd /srv/tac-master && uv run orchestrator/daemon.py --doctor'
```

Expected output ends with `✓ all checks passed`.

## 6. Start the service

```bash
systemctl start tac-master
systemctl status tac-master
journalctl -u tac-master -f
```

## 7. Webhook setup (optional, reduces poll latency)

On each allowlisted repo in GitHub:
- **Payload URL**: `https://<your-host>/tac-master/webhook`
- **Content type**: `application/json`
- **Secret**: value of `GITHUB_WEBHOOK_SECRET` in identity.env
- **Events**: Issues, Issue comments

You'll need to expose a port via your reverse proxy / Cloudflare tunnel /
Tailscale. The webhook listener lives in `adws/adw_triggers/trigger_webhook.py`
(lifted from tac-7) and is NOT started by default — the systemd unit only
runs the polling daemon. To enable webhooks, add a second systemd unit
pointing at the webhook script.

## 8. Operations

| Action | Command |
|---|---|
| Tail daemon logs | `journalctl -u tac-master -f` |
| Tail a specific run | `tail -f /srv/tac-master/logs/run_<adw_id>.log` |
| Restart | `systemctl restart tac-master` |
| Stop gracefully | `systemctl stop tac-master` (waits up to 120s for current cycle) |
| Dry-run poll | `sudo -u krypto bash -lc 'cd /srv/tac-master && uv run orchestrator/daemon.py --once --dry-run'` |
| Update code | `cd /srv/tac-master && sudo -u krypto git pull && systemctl restart tac-master` |
| Inspect SQLite | `sqlite3 /srv/tac-master/state/tac_master.sqlite` |
| List active runs | `sqlite3 .../tac_master.sqlite "SELECT adw_id, repo_url, issue_number, status FROM runs WHERE status IN ('pending', 'running');"` |
| Check budget | `sqlite3 .../tac_master.sqlite "SELECT * FROM budget_usage WHERE day=date('now');"` |

## 9. Security notes

- The LXC container is unprivileged — even if an ADW exfiltrates a shell,
  it can't escape to the host.
- `krypto-agent`'s PAT should be **fine-grained** and limited to the
  allowlist. Rotate on any incident.
- `config/identity.env` is `chmod 600` owned by `krypto`.
- The systemd unit enables `NoNewPrivileges`, `ProtectSystem=full`,
  `ProtectHome=read-only`. Write access is confined to `/srv/tac-master`.
- Per user policy, tac-master has **full auto-merge** on its own repo and
  assigned repos. The only safety net is:
  1. The fine-grained PAT allowlist
  2. Budget circuit breakers in `budgets.yaml`
  3. `policies.yaml` forbidden-paths and max_diff_lines caps
  4. Post-merge health check with rollback (for self-improvements only)
