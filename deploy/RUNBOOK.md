# tac-master Operator Runbook

**Service**: tac-master (polling daemon + webhook + dashboard)
**Host**: LXC 114 — `10.0.10.22`
**Last updated**: 2026-04-09

This document is for on-call operators. It covers recovery for the five known failure modes and includes quick-reference commands for log tailing, SQL inspection, and health checks.

---

## Quick Reference

### Log tailing

```bash
# Follow the systemd journal (includes service restarts and crashes)
journalctl -u tac-master -f

# Follow the daemon's stdout log directly (ADW run output appears here)
tail -f /srv/tac-master/logs/daemon.stdout.log

# Follow the webhook service log
tail -f /srv/tac-master/logs/webhook.stdout.log

# Tail a specific ADW run log
tail -f /srv/tac-master/logs/run_<adw_id>.log
```

### SQLite inspection

```bash
# Last 10 runs (most recent first)
sqlite3 /srv/tac-master/state/tac_master.sqlite \
  'SELECT * FROM runs ORDER BY started_at DESC LIMIT 10;'

# All currently-running runs
sqlite3 /srv/tac-master/state/tac_master.sqlite \
  'SELECT id, issue_num, status, started_at FROM runs WHERE status = "running";'

# Failed issues that need attention
sqlite3 /srv/tac-master/state/tac_master.sqlite \
  'SELECT issue_num, status, updated_at FROM issues WHERE status = "failed";'

# Full issue state
sqlite3 /srv/tac-master/state/tac_master.sqlite \
  'SELECT * FROM issues ORDER BY updated_at DESC LIMIT 20;'
```

### Service management

```bash
# Restart all three services
systemctl restart tac-master tac-master-webhook tac-master-dashboard

# Restart daemon only
systemctl restart tac-master

# Check service status
systemctl status tac-master tac-master-webhook tac-master-dashboard

# Update the full stack from git
sudo bash /srv/tac-master/scripts/tac-update.sh update
```

### Doctor command

Run this to verify the daemon's environment (dependencies, config, API keys, DB connectivity):

```bash
sudo -u krypto bash -lc 'cd /srv/tac-master && uv run orchestrator/daemon.py --doctor'
```

Expected healthy output:

```
[doctor] Checking environment...
[doctor] uv: OK (/home/krypto/.local/bin/uv)
[doctor] claude: OK (/home/krypto/.local/bin/claude)
[doctor] Config: OK (identity.env, repos.yaml loaded)
[doctor] DB: OK (/srv/tac-master/state/tac_master.sqlite, schema v<N>)
[doctor] GitHub API: OK (CleoAgent authenticated)
[doctor] Anthropic API: OK (key valid)
[doctor] All checks passed.
```

If any check shows `FAIL` or `ERROR`, follow the section below that matches the symptom.

---

## Failure Mode 1 — Stuck Run (status=running for >20 min)

### Symptom

An ADW run has been in `status=running` for more than 20 minutes with no recent log output. The daemon is otherwise healthy (polling loop continues, other runs complete normally). The GitHub issue has no new comments from CleoAgent.

### Reproduce

```bash
# Find runs that have been running longer than 20 minutes
sqlite3 /srv/tac-master/state/tac_master.sqlite \
  "SELECT id, issue_num, started_at,
          round((julianday('now') - julianday(started_at)) * 1440) AS minutes_running
   FROM runs
   WHERE status = 'running'
   ORDER BY started_at ASC;"
```

Any row with `minutes_running >= 20` is stuck.

### Recovery

**Automatic recovery (built-in):** The daemon's `reap_finished_runs` loop runs every polling cycle and calls `_kill_run_tree()` on any run that has been `running` for more than 20 minutes. This sends `SIGKILL` to the entire process group (the run is spawned with `start_new_session=True` so `os.killpg` reaches all child processes), then marks the run `failed` in the database. In most cases the daemon will self-heal within one polling interval (~20 s).

**Manual recovery (if the daemon itself is stuck or the automatic kill did not fire):**

1. Find the PID of the stuck ADW process:

   ```bash
   # List all processes owned by krypto
   ps -u krypto -o pid,ppid,stat,wchan,cmd --no-headers | grep -v 'ps\|grep'
   ```

2. Identify the process group of the stuck run (look for `python` or `claude` processes with a suspicious wchan such as `ep_poll` or `pipe_wait`):

   ```bash
   cat /proc/<pid>/status | grep -E 'Name|Pid|PPid|State|Tgid|NSpid'
   # Check what fd the process has open (inherited stdin/pipe issues show here)
   ls -la /proc/<pid>/fd/
   ```

3. Kill the entire process group:

   ```bash
   # Get the process group ID (usually same as PID for session leader)
   PGID=$(ps -o pgid= -p <pid> | tr -d ' ')
   kill -KILL -$PGID
   ```

4. Mark the run failed in the database (the daemon will do this automatically on next reap cycle, but you can force it):

   ```bash
   sqlite3 /srv/tac-master/state/tac_master.sqlite \
     "UPDATE runs SET status='failed', finished_at=datetime('now') WHERE id='<run_id>';"
   sqlite3 /srv/tac-master/state/tac_master.sqlite \
     "UPDATE issues SET status='failed', updated_at=datetime('now') WHERE issue_num=<issue_num>;"
   ```

5. Restart the daemon to ensure a clean state:

   ```bash
   systemctl restart tac-master
   ```

---

## Failure Mode 2 — EROFS Errors in Logs

### Symptom

Claude subprocess output in `daemon.stdout.log` or a run log contains:

```
EROFS: read-only file system, open '/home/krypto/.claude.json'
```

The ADW run fails immediately with `duration_api_ms: 0` and subtype `error_during_execution`. No tokens are consumed and no GitHub comment is posted.

**Root cause:** The systemd unit has `ProtectHome=read-only`, which makes `/home/krypto/` read-only for the service. Claude attempts to write its config/session cache to `~/.claude.json` and `~/.claude/` on startup and is blocked by the kernel.

### Reproduce

1. Confirm the read-only protection is active:

   ```bash
   systemctl show tac-master -p ProtectHome
   # Expected: ProtectHome=read-only
   ```

2. Confirm which paths are whitelisted for writes:

   ```bash
   systemctl show tac-master -p ReadWritePaths
   # Expected output includes /home/krypto/.claude /home/krypto/.claude.json /home/krypto/.cache
   ```

   If `/home/krypto/.claude`, `/home/krypto/.claude.json`, or `/home/krypto/.cache` are missing from `ReadWritePaths`, that is the bug.

3. Confirm by searching recent logs:

   ```bash
   journalctl -u tac-master --since "1 hour ago" | grep -i EROFS
   ```

### Recovery

1. Edit the systemd unit on the LXC:

   ```bash
   # Preferred: edit the deployed unit file directly
   nano /srv/tac-master/deploy/systemd/tac-master.service
   ```

   Ensure the `[Service]` section contains:

   ```ini
   ProtectHome=read-only
   ReadWritePaths=/srv/tac-master /home/krypto/.claude /home/krypto/.claude.json /home/krypto/.cache
   ```

   Also apply the same fix to `tac-master-webhook.service` if it runs claude subprocesses.

2. Reload and restart:

   ```bash
   systemctl daemon-reload
   systemctl restart tac-master tac-master-webhook
   ```

3. Verify the fix:

   ```bash
   systemctl show tac-master -p ReadWritePaths
   # Confirm all three home paths appear
   ```

4. Trigger a test run by commenting `adw` on a GitHub issue in an allowlisted repo and watch the log:

   ```bash
   journalctl -u tac-master -f
   ```

   A healthy run will show claude output flowing into the log within ~30 s of the dispatch.

**Note:** If the systemd unit file was replaced (e.g., after a `tac-update.sh update`), the `ReadWritePaths` entries may have been reverted to a stale version. Always re-check after updates.

---

## Failure Mode 3 — Subprocess Hang (stdin / stderr / epoll_wait)

### Symptom

A run starts (PID visible in `ps`) but produces no output and never finishes. CPU usage is 0%. The process is stuck in a kernel wait. This is distinct from a slow run — a slow run shows periodic log output; a hung run is completely silent.

Common wchan values for hung processes:

- `ep_poll` — blocked on `epoll_wait`, usually stdin TTY detection
- `pipe_wait` — blocked waiting for a pipe reader/writer, usually stderr deadlock
- `futex_wait` — mutex contention (less common)

**Root cause (historical):** Two separate bugs caused this class of hang. Both are fixed in the current codebase, but the diagnostic procedure below is useful if a regression occurs or a new subprocess is introduced.

- **stdin hang** (`ep_poll`): Claude's init probes stdin for TTY detection even with `--dangerously-skip-permissions`. If stdin is inherited from a parent process that used `start_new_session=True`, claude blocks indefinitely on `epoll_wait`. Fix: `stdin=subprocess.DEVNULL` in the claude subprocess call.
- **stderr deadlock** (`pipe_wait`): Using `stderr=subprocess.PIPE` with `subprocess.run` causes a deadlock when claude's verbose output fills the 64 KB kernel pipe buffer. `subprocess.run` only drains stderr after the child exits, so the child blocks on `write()` and never exits. Fix: `stderr=subprocess.STDOUT` routes both streams to the unbounded output file.

### Reproduce

1. Find the stuck process:

   ```bash
   ps -u krypto -o pid,stat,wchan,cmd --no-headers | grep -v 'ps\|grep'
   # Look for processes with wchan of ep_poll or pipe_wait
   ```

2. Inspect the stuck process:

   ```bash
   # Check kernel wait channel (confirms the hang type)
   cat /proc/<pid>/status | grep wchan
   # or:
   cat /proc/<pid>/wchan

   # Check open file descriptors (stdin=0, stdout=1, stderr=2)
   ls -la /proc/<pid>/fd/
   # Healthy: fd/0 -> /dev/null, fd/1 and fd/2 -> a file or pipe
   # Unhealthy (stdin hang): fd/0 -> pipe:[xxxxxx] (inherited pipe, not /dev/null)
   # Unhealthy (stderr deadlock): fd/2 -> pipe:[xxxxxx] with no reader
   ```

3. Walk up the process tree to find the ADW run that spawned it:

   ```bash
   ps -o pid,ppid,cmd --no-headers -p <pid>
   # Follow PPIDs up until you find the run ID in the cmd or cwd
   ```

### Recovery

**Immediate:**

```bash
# Kill the entire process group
PGID=$(ps -o pgid= -p <pid> | tr -d ' ')
kill -KILL -$PGID

# Mark the run failed (see Failure Mode 1 step 4)
```

**Verify the fix is still in place (regression check):**

```bash
# Confirm stdin=DEVNULL and stderr=STDOUT are set in agent.py
grep -n 'stdin\|stderr' /srv/tac-master/adws/adw_modules/agent.py
# Expected lines:
#   stdin=subprocess.DEVNULL,
#   stderr=subprocess.STDOUT,
```

If these lines are missing (e.g., after a botched merge), restore them and restart the daemon.

---

## Failure Mode 4 — Substrate Staleness (fix doesn't propagate to clones)

### Symptom

A bug fix or configuration change was merged to `main` of the tac-master repo. After the fix, new runs still exhibit the old behavior. The daemon logs show runs starting and completing, but the output reflects code from before the fix.

**Root cause:** Each dispatch creates a git worktree clone under `/srv/tac-master/repos/<owner>_<repo>/trees/<adw_id>/`. The clone's `adws/`, `.claude/`, and `ai_docs/` directories are injected at clone time from the substrate at `/srv/tac-master/adws/` etc. If the substrate itself was not updated (e.g., `git pull` was not run, or the update script was not executed), the clones inherit stale code.

### Reproduce

```bash
# Check the current HEAD of the deployed repo
cd /srv/tac-master && git log --oneline -5

# Compare against GitHub
git fetch origin && git log --oneline HEAD..origin/main
# Any commits listed here are not yet deployed
```

For an existing clone, check the injected substrate version:

```bash
# Find a recent clone
ls -lt /srv/tac-master/repos/ | head -5

# Check the adws version inside the clone
cat /srv/tac-master/repos/<owner>_<repo>/trees/<adw_id>/adws/adw_modules/agent.py | head -20
# Compare a known fixed line (e.g., stdin=subprocess.DEVNULL)
```

### Recovery

1. Pull the latest code:

   ```bash
   sudo bash /srv/tac-master/scripts/tac-update.sh update
   ```

   Or manually:

   ```bash
   cd /srv/tac-master
   sudo -u krypto git pull origin main
   systemctl restart tac-master tac-master-webhook tac-master-dashboard
   ```

2. Delete stale clones so the next dispatch gets fresh substrate injection:

   ```bash
   # Delete a specific stale clone
   rm -rf /srv/tac-master/repos/<owner>_<repo>/

   # Delete ALL clones (safe — they are recreated on next dispatch)
   rm -rf /srv/tac-master/repos/*/
   ```

3. Verify by triggering a new run and confirming the fixed behavior appears.

**Note:** Task T002 is implementing a permanent fix that will detect and invalidate stale clones automatically. Until that ships, manual clone deletion is the recovery path after any substrate change.

---

## Failure Mode 5 — Stuck Failed Issue (no auto-retry)

### Symptom

An issue has `status=failed` in the `issues` table and has not been retried. The GitHub issue has no new activity from CleoAgent. The operator expects the system to retry but it does not.

**Root cause:** The dispatcher's `_should_dispatch` function treats `failed` as a terminal status and will not re-dispatch a failed issue automatically. This prevents re-dispatch spam after transient errors, but it also means genuine retries require manual intervention.

### Reproduce

```bash
# Find all failed issues
sqlite3 /srv/tac-master/state/tac_master.sqlite \
  'SELECT issue_num, status, updated_at FROM issues WHERE status = "failed";'

# Cross-check against recent runs for the issue
sqlite3 /srv/tac-master/state/tac_master.sqlite \
  'SELECT id, status, started_at, finished_at FROM runs WHERE issue_num = <issue_num> ORDER BY started_at DESC LIMIT 5;'
```

### Recovery

**Option A — Re-trigger via GitHub comment (preferred):**

Post a comment containing `adw` on the GitHub issue. The webhook or next polling cycle will pick it up, the dispatcher will treat it as a new trigger event, and it will reset the issue status and dispatch a new run.

```
# On the GitHub issue page, post a comment:
adw
```

**Option B — Reset the database row manually:**

```bash
sqlite3 /srv/tac-master/state/tac_master.sqlite \
  "UPDATE issues SET status='open', updated_at=datetime('now') WHERE issue_num=<issue_num>;"
```

After the update, the next polling cycle (~20 s) will re-evaluate the issue and dispatch a new run if the trigger label is still present.

**Option C — Delete the issue row:**

This is a harder reset. The daemon will re-discover the issue from the GitHub API as if it were new.

```bash
sqlite3 /srv/tac-master/state/tac_master.sqlite \
  "DELETE FROM issues WHERE issue_num=<issue_num>;"
```

**Note:** Task T012 is implementing an automated retry affordance (configurable retry count and backoff). Until that ships, one of the manual options above is required.

---

## Appendix — Paths and Services

| Item | Path / Value |
|------|-------------|
| Daemon code | `/srv/tac-master/` |
| SQLite state | `/srv/tac-master/state/tac_master.sqlite` |
| Dashboard SQLite | `/srv/tac-master/state/dashboard.sqlite` |
| Clone repos | `/srv/tac-master/repos/<owner>_<repo>/` |
| Logs | `/srv/tac-master/logs/` |
| Config | `/srv/tac-master/config/` |
| Claude config | `/home/krypto/.claude/` and `/home/krypto/.claude.json` |
| uv cache | `/srv/tac-master/state/uv-cache/` |
| Daemon service | `tac-master.service` |
| Webhook service | `tac-master-webhook.service` |
| Dashboard service | `tac-master-dashboard.service` |
| Dashboard UI | `http://10.0.10.22:4000/` |
| Webhook endpoint | `http://10.0.10.22:8088/webhook/github` |
| Webhook health | `http://10.0.10.22:8088/health` |
