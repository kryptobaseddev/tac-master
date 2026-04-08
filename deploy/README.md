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

## 9. Headless Claude Code operation

**tac-master runs Claude Code CLI as a pure subprocess with no TTY, no
browser, and no interactive login.** This is a first-class supported mode
of the CLI. You do **not** need to run `claude login` on the LXC.

### How it works

Every ADW phase (plan / build / test / review / document / ship) invokes
the `claude` binary via `adws/adw_modules/agent.py` with these flags:

```
claude -p "<prompt>" \
       --model <sonnet|opus|haiku> \
       --output-format stream-json \
       --verbose \
       --dangerously-skip-permissions \
       [--mcp-config .mcp.json]
```

- `-p` is **print mode** — single prompt, machine-readable output, exits
  when done. Zero interactive surface.
- `--output-format stream-json` produces a JSONL event stream that
  tac-master's `token_tracker.py` parses for cost accounting.
- `--dangerously-skip-permissions` bypasses tool-permission prompts.
  Safety is enforced upstream via `config/policies.yaml` (forbidden
  paths, diff caps) and the fine-grained PAT allowlist.
- `--mcp-config .mcp.json` loads the Playwright MCP server for the
  review phase (screenshots + visual regression).

Authentication is via the `ANTHROPIC_API_KEY` environment variable,
which the systemd unit reads from `config/identity.env` and propagates to
every child process.

### No `claude login` required

The CLI's login flow stores credentials in `~/.claude/.credentials.json`
for interactive use with an Anthropic subscription. tac-master does not
use this flow — it uses API-key auth instead. This has three advantages:

1. **Works in headless containers** with no GUI / no browser
2. **No session expiry** — API keys don't refresh
3. **Per-token budget attribution** — the API returns usage/cost in the
   stream, which tac-master accumulates into `token_ledger`

### Why the CLI and not the raw Agent SDK?

The Agent SDK is the runtime library underneath Claude Code CLI. The CLI
adds four things tac-master depends on:

1. **Slash commands** — `.claude/commands/*.md` (28 of them)
2. **Hooks** — `.claude/hooks/*.py` (7 lifecycle hooks)
3. **MCP auto-loading** via `--mcp-config`
4. **Subagent support** via `.claude/agents/`

Replacing the CLI with the raw SDK would require reimplementing all of
this as Python code. The CLI runs cleanly headless, so there's no reason
to do that rewrite.

### Verifying the install

```bash
# As the service user:
sudo -u krypto bash -lc 'claude --version'
# Should print something like: 1.0.x (Claude Code)

# Then run the doctor, which exercises the full env:
sudo -u krypto bash -lc 'cd /srv/tac-master && uv run orchestrator/daemon.py --doctor'
# Looks for: claude code: ✓ ... (path)
#            playwright: ✓ cache at /home/krypto/.cache/ms-playwright
#            mcp config .mcp.json: ✓
```

### Optional: using an Anthropic subscription instead of API key

If you'd rather bill the bot's usage to an Anthropic Pro/Max subscription
tied to a Google/email account, you have to:

1. SSH into the LXC with X11 forwarding or use a VNC session
2. Run `sudo -u krypto claude login` interactively once
3. The token lands in `~krypto/.claude/.credentials.json`

**Not recommended** for autonomous operation:
- Sessions can expire and require re-login (which halts the bot)
- No per-run cost attribution (the SDK only returns usage for API-key auth)
- You'd need to drop `ANTHROPIC_API_KEY` from `identity.env` so the CLI
  falls back to credential auth

For a long-running autonomous agent on a server, **stick with the API
key** unless you have a strong reason to use a subscription.

## 10. Ongoing updates

A single script manages the entire dependency stack:

```bash
sudo bash /srv/tac-master/scripts/tac-update.sh check           # report versions
sudo bash /srv/tac-master/scripts/tac-update.sh update          # update + restart
sudo bash /srv/tac-master/scripts/tac-update.sh update --dry-run
sudo bash /srv/tac-master/scripts/tac-update.sh update --system-only
sudo bash /srv/tac-master/scripts/tac-update.sh update --no-restart
```

`update` touches: apt, Node.js, Claude Code CLI, uv, Bun, Podman (if
installed), Playwright Chromium, GitHub CLI, tac-master itself (`git
pull`), and the dashboard client (`npm run build`). Then restarts the
three systemd units and runs `--doctor`.

Run it from cron for hands-off upkeep:

```cron
# /etc/cron.d/tac-master-update — weekly, Sunday 04:00
0 4 * * 0 root /srv/tac-master/scripts/tac-update.sh update >> /srv/tac-master/logs/update.log 2>&1
```

## 11. Optional: Podman runtime mode

By default tac-master runs ADWs as native subprocesses on the LXC host.
This is fast and simple, but all runs share the same toolchain (one
Node version, one Python version, one uv cache, etc.).

If your allowlisted repos have **heterogeneous runtime requirements**
(different Node majors, legacy Ruby + modern Go + Java, etc.), enable
the Podman runtime mode. Each run wraps its ADW in a rootless podman
container:

- Worktree bind-mounted at `/workspace` (rw)
- `adws/` and `.claude/` bind-mounted read-only
- Fresh filesystem per run (no cache leaks)
- Per-repo `container_image` in `repos.yaml` so each repo brings its
  own Dockerfile if needed
- Network mode `host` so dashboard hooks still reach port 4000

### Enabling Podman mode

```bash
# One-time install: apt packages + subuid/subgid + build tac-worker:latest
sudo bash /srv/tac-master/scripts/tac-update.sh install-podman
```

This installs `podman`, `fuse-overlayfs`, `slirp4netns`, `uidmap`,
`buildah`, `skopeo`, `passt`, configures subuid/subgid for the krypto
user, and builds `tac-worker:latest` from
`deploy/docker/Dockerfile.tac-worker` (takes ~5 minutes — downloads
Node, Claude Code CLI, and Playwright Chromium into the image).

Then mark the repos that should use it in `config/repos.yaml`:

```yaml
- url: https://github.com/OWNER/legacy-ruby-app
  runtime: podman
  container_image: tac-worker:latest   # or a per-repo image you build
```

Run the doctor to verify:

```bash
sudo -u krypto bash -lc 'cd /srv/tac-master && uv run orchestrator/daemon.py --doctor'
# Expect:
#   runtime: 1 repo(s) use podman — verifying...
#   podman: ✓ podman version 4.x.x
#   image tac-worker:latest: ✓
```

Then restart: `systemctl restart tac-master`.

### Per-repo custom images

If a specific repo needs its own toolchain (e.g. Ruby 3.2 + PostgreSQL
client), build a custom image FROM `tac-worker:latest`:

```dockerfile
# /srv/tac-master/deploy/docker/Dockerfile.ruby-app
FROM tac-worker:latest
USER root
RUN apt-get update && apt-get install -y ruby-full postgresql-client \
    && rm -rf /var/lib/apt/lists/*
USER worker
```

Build as the krypto user:

```bash
sudo -u krypto podman build \
    -t tac-worker-ruby:latest \
    -f /srv/tac-master/deploy/docker/Dockerfile.ruby-app \
    /srv/tac-master
```

Then point the repo at it in `repos.yaml`:

```yaml
- url: https://github.com/OWNER/ruby-app
  runtime: podman
  container_image: tac-worker-ruby:latest
```

### Caveats

- **Cold-start overhead**: ~2-5 seconds extra per dispatch vs. native
- **User namespaces**: the Proxmox LXC must allow userns. Unprivileged
  LXCs usually do by default. If `podman info` fails with `newuidmap:
  write to uid_map failed`, set `lxc.include = /usr/share/lxc/config/nesting.conf`
  and `features=keyctl=1,nesting=1` on the container config.
- **Storage**: rootless podman images live in
  `/home/krypto/.local/share/containers/`. Give the LXC enough disk
  (+10 GB for a single `tac-worker` image).
- **Mixed mode**: it's fine to have some repos on `runtime: native` and
  others on `runtime: podman`. The dispatcher picks the right runner
  per-dispatch.

## 12. Security notes

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
