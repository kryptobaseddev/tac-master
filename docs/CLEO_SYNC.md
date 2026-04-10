# CLEO tasks.db Sync — T035 / T072

**Status**: Implemented
**Tasks**: T035, T072 (bidirectional sync + dashboard CLEO API)

---

## Overview

The tac-master dashboard reads CLEO task data (epics, tasks, statuses) to render the
Epic progress panel and orchestrator hierarchy view. CLEO's authoritative database
(`tasks.db`) lives on the dev machine. The dashboard server runs on LXC 114.

A 30-second systemd user-timer on the dev machine synchronises the two copies
bidirectionally:

```
Dev machine (keatonhoskins@10.0.10.146)         LXC 114 (root@10.0.10.22)
 ~/.cleo/tasks.db                                /srv/tac-master/state/cleo-tasks.db
         │                                                   │
         │  1. WAL checkpoint (dev)                          │
         │  2. Pull if LXC newer (write-back)                │
         │  3. Push dev copy ─────────────────────────────► │
         │  4. Write .cleo_sync_ts                           │
```

---

## Sync Mechanism

### Direction A: Dev → LXC (primary, every 30 s)

Script: `tac-master/scripts/cleo-sync.sh`

1. Reads LXC DB modification time via SSH.
2. If LXC is newer than dev (daemon wrote status updates), pulls LXC copy back to
   dev (write-back), WAL-checkpoints it, then replaces dev copy.
3. WAL checkpoints the dev copy so the main `.db` file contains all committed state.
4. Rsyncs the dev copy to `/srv/tac-master/state/cleo-tasks.db` on LXC.
5. Removes stale WAL/SHM files on LXC that would cause SQLite confusion.
6. Fixes file ownership to `krypto:krypto` so the dashboard server can read the file.
7. Writes `/srv/tac-master/state/.cleo_sync_ts` with the current UTC timestamp.

### Direction B: LXC → Dev (write-back, within the same timer cycle)

When the tac-master daemon (on LXC) calls `cleo update TASK_ID --status done`, the
`cleo` CLI writes to the LXC copy at `/srv/tac-master/state/cleo-tasks.db`. On the
next sync cycle, the script detects that the LXC copy is newer and pulls it back to
the dev machine before pushing the merged state out again.

Write-back latency: at most 30 seconds (one timer cycle).

---

## Files

| File | Purpose |
|------|---------|
| `scripts/cleo-sync.sh` | Bidirectional sync script |
| `deploy/systemd/cleo-sync.service` | Systemd oneshot service (dev machine, user-level) |
| `deploy/systemd/cleo-sync.timer` | Systemd timer — fires every 30 s |
| `/srv/tac-master/state/cleo-tasks.db` | LXC-side synced copy (read by dashboard) |
| `/srv/tac-master/state/.cleo_sync_ts` | UTC timestamp of last successful sync |

---

## Installation

### Dev machine (user-level systemd — no sudo required)

```bash
# Copy unit files to user systemd directory
cp deploy/systemd/cleo-sync.service ~/.config/systemd/user/
cp deploy/systemd/cleo-sync.timer ~/.config/systemd/user/

# Reload and enable
systemctl --user daemon-reload
systemctl --user enable cleo-sync.timer
systemctl --user start cleo-sync.timer

# Verify
systemctl --user status cleo-sync.timer
journalctl --user -u cleo-sync.service -n 20
```

### SSH credentials

The sync script reads SSH credentials from `/mnt/projects/agentic-engineer/.tac-master-creds.env`:

```bash
SSH_USER=root
SSH_PASSWORD=...
LXC_IP=10.0.10.22
```

The service unit includes `EnvironmentFile=-/mnt/projects/agentic-engineer/.tac-master-creds.env`.

### SSH key (passwordless, for future use)

An ed25519 key pair was generated on LXC at `/root/.ssh/cleo_sync_key`.
The public key is in the dev machine's `~/.ssh/authorized_keys`, ready for
use once SSH is enabled on the dev machine.

---

## Operations

### Run sync manually

```bash
# From dev machine
systemctl --user start cleo-sync.service

# Or run the script directly
/mnt/projects/agentic-engineer/tac-master/scripts/cleo-sync.sh

# On LXC — trigger a sync by making the LXC copy appear newer:
# touch /srv/tac-master/state/cleo-tasks.db
```

### Check sync status

```bash
# Timer status
systemctl --user status cleo-sync.timer
systemctl --user list-timers cleo-sync*

# Recent sync logs
journalctl --user -u cleo-sync.service -n 30

# Check LXC timestamp
ssh root@10.0.10.22 cat /srv/tac-master/state/.cleo_sync_ts
```

### Staleness indicator in the dashboard

The `GET /api/cleo/epics` endpoint returns:

```json
{
  "epics": [...],
  "dbPath": "/srv/tac-master/state/cleo-tasks.db",
  "stale": false,
  "stale_seconds": 12
}
```

`stale` is `true` if the last sync was more than 60 seconds ago. The frontend
can display "Last synced: N seconds ago" using `stale_seconds`.

### What to do if sync falls behind

1. Check the timer is running: `systemctl --user status cleo-sync.timer`
2. Check for SSH failures: `journalctl --user -u cleo-sync.service -n 30`
3. Verify LXC reachability: `ping 10.0.10.22`
4. Run manually to check the error: `/mnt/projects/agentic-engineer/tac-master/scripts/cleo-sync.sh`
5. If the LXC copy is corrupt (WAL mismatch), delete it and let the next sync recreate it:
   `ssh root@10.0.10.22 rm /srv/tac-master/state/cleo-tasks.db && systemctl --user start cleo-sync.service`

---

## Architecture Decision

See `.cleo/outputs/T034_cleo_sync_strategy.md` for the full options analysis.
Option A (rsync + systemd timer) was chosen for simplicity and zero new infrastructure.

### Deviation from spec

The T034 spec specified the timer should run on LXC (LXC pulls from dev). This was
not implemented because the dev machine has no SSH server running. Instead, the timer
runs on the dev machine as a user-level service, which achieves the same 30-second
sync interval with no additional infrastructure.

The LXC also has an ed25519 key generated and the dev machine has that key in
`~/.ssh/authorized_keys` — if SSH is later enabled on the dev machine, the sync can
be migrated to the pull model without script changes (just update the rsync command).
