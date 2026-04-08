# tac-master Architecture

This document describes the runtime data flow and design decisions that
aren't obvious from reading the code.

## Process topology

```
┌─────────────────────────────────────────────────────────────────────┐
│  systemd: tac-master.service                                        │
│                                                                     │
│   PID 1: orchestrator/daemon.py (loop)                              │
│      ├─ dispatcher.poll_once() every 20s                            │
│      ├─ dispatcher.reap_finished_runs() every 20s                   │
│      └─ spawns detached Leads ──┐                                   │
└─────────────────────────────────┼───────────────────────────────────┘
                                  │
     ┌────────────────────────────┼───────────────────────────┐
     ▼                            ▼                           ▼
 Lead (adw_id=abc1)          Lead (adw_id=def2)          Lead (adw_id=ghi3)
 cwd=repos/acme_api/         cwd=repos/acme_api/         cwd=repos/acme_web/
 worktree=trees/abc1/        worktree=trees/def2/        worktree=trees/ghi3/
     │                            │                           │
     ▼                            ▼                           ▼
 spawns subprocess chain:
   adw_plan_iso   (claude CLI)
     → adw_build_iso  (claude CLI)
       → adw_test_iso  (claude CLI)
         → adw_review_iso  (claude CLI)
           → adw_document_iso  (claude CLI)
             → adw_ship_iso  (claude CLI)
               → adw_reflect_iso  (pure Python, writes lesson)
```

The Orchestrator daemon is stateful (SQLite + config) but **stateless
between cycles** in the sense that it can be restarted at any time and
will rediscover active runs by their pid + adw_state.json on disk.

Leads are fully independent: killing the daemon does not kill in-flight
Leads. This is intentional — it lets you upgrade the daemon without
interrupting work.

## The substrate symlink trick

tac-7's ADW scripts assume they live inside the target project at
`<project_root>/adws/` and compute `project_root` as three directories
above `adw_modules/state.py`. We preserve this assumption without
requiring every target repo to ship the substrate by:

1. **Cloning** each target repo into `/srv/tac-master/repos/<owner>_<repo>/`.
2. **Symlinking** `tac-master/adws`, `tac-master/.claude`, and
   `tac-master/ai_docs` into the clone root.
3. **Adding exclusions** to `.git/info/exclude` (per-clone, not committed)
   so the symlinks never appear in diffs or PRs.
4. **Pre-creating** the worktree at `/srv/tac-master/trees/<slug>__<adw_id>/`.
5. **Symlinking** the substrate into the worktree too (git worktree add
   does a fresh checkout, so symlinks from the clone don't follow).
6. **Symlinking** the worktree INTO the clone's `trees/<adw_id>/` so
   tac-7's `worktree_ops.validate_worktree()` finds it where it expects.

This looks like magic but it's three `ln -s` calls and a handful of
`.git/info/exclude` entries per dispatch. The alternative — patching every
ADW script to accept `--repo-path` — would diverge from tac-7 permanently.

## State boundaries

There are **two separate state systems**, deliberately decoupled:

### 1. Per-ADW state (tac-7 native)

- Lives at `<worktree>/agents/<adw_id>/adw_state.json`
- Owned by the ADW scripts; orchestrator never writes to it
- Contains: issue number, branch name, plan file path, worktree path,
  backend/frontend ports, model set, completed phases list
- Survives process restarts inside the same worktree
- Schema: `adw_modules/data_types.py > ADWStateData`

### 2. Global orchestrator state (tac-master native)

- Lives at `<home>/state/tac_master.sqlite`
- Owned by the orchestrator; ADWs never touch it
- Contains: repos, issues, runs, phases, events, budget_usage
- Used for: dispatching decisions, concurrency caps, budget enforcement,
  dashboard feed, reflection lookups
- Schema: `orchestrator/state_store.py > SCHEMA`

The orchestrator **reads** per-ADW state files when reaping completed runs
(to infer success/failure) but never mutates them.

## Dispatch decision flow

```
poll_once()
  for each repo in allowlist:
    upsert_repo(repo)
    issues = github.list_open_issues(repo, labels=repo.trigger_labels)
    for each issue:
      should_dispatch?
        ├─ check triggers (new_issue, comment_adw, label)
        ├─ check seen_issue status in SQLite
        └─ decision: dispatch | skip
      if dispatch:
        budget.can_dispatch(repo)
          ├─ global concurrent cap
          ├─ per-repo concurrent cap
          ├─ global daily token cap
          ├─ global daily run cap
          ├─ per-repo daily token cap
          └─ per-repo daily run cap
        if allowed:
          repo_mgr.ensure_clone(repo)      # idempotent
          repo_mgr.sync(repo)              # git fetch
          adw_id = secrets.token_hex(4)
          repo_mgr.create_worktree(repo, adw_id)
          subprocess.Popen(adws/<workflow>.py <issue> <adw_id>)
          store.create_run(adw_id)
          store.set_issue_status(dispatched)
          budget.record_dispatch(repo)
```

## Budget model

Budgets live in SQLite `budget_usage` keyed by `(day, repo_url)`. A
`__global__` row aggregates across all repos. On each dispatch, the
enforcer checks six caps in order (cheapest first), refusing dispatch on
the first failure and logging a `budget` event.

Token counts are accumulated post-hoc: after a run completes, its
claude-code JSONL output files under `agents/<adw_id>/` are scanned for
token usage and added to the budget table. (This happens in
`dispatcher.reap_finished_runs()` → TODO: needs the actual JSONL parser
wired up.)

**Known gap**: tokens are billed AFTER completion, so a runaway in-flight
run can exceed its budget briefly. Mitigations: `max_tokens_per_run` in
`budgets.yaml.defaults` caps individual runs (enforced by the ADW's own
agent.py retry logic), and the global concurrent cap bounds total
simultaneous burn.

## Self-improvement safety

Per user policy, tac-master has **full write + auto-merge** authority on
its own repo. The safety net is:

1. **Fine-grained PAT** — `krypto-agent`'s token is scoped to only
   allowlisted repos. Anything outside the allowlist is impossible by
   construction.
2. **Forbidden paths** — `policies.yaml > safety > forbidden_paths`
   prevents touching `.env`, keys, credentials in any repo.
3. **Diff caps** — `max_files_per_pr` and `max_diff_lines_per_pr` reject
   overly large self-modifications.
4. **Post-merge health check** — after merge, the orchestrator runs
   `daemon.py --doctor` via subprocess. On failure within
   `timeout_minutes`, the merge is reverted (`git revert`).
5. **Rollback lesson** — every reverted self-change writes a lesson
   explaining what went wrong, which future Leads see.

## Failure modes and recovery

| Failure | Detection | Recovery |
|---|---|---|
| Daemon crash | systemd `Restart=always` | Auto-restart in 10s; in-flight Leads unaffected |
| Lead crash | `reap_finished_runs` sees pid gone, adw_state.json incomplete | Mark `failed`, leave worktree for inspection |
| Worker crash | Lead subprocess exits non-zero | Lead records failed phase, exits, reaped normally |
| GitHub API rate limit | 403 in `github_client` | Logged, skip cycle, retry next poll |
| Anthropic API down | claude CLI returns error | tac-7's `agent.py` retry logic (up to 3x) |
| Self-modification breaks daemon | Post-merge health check fails | Auto-revert; lesson recorded |
| Budget exceeded mid-run | Checked at dispatch, not mid-flight | Run finishes, no new runs dispatched |
| Worktree corruption | `git worktree add` fails | Cleanup and retry on next poll |

## Open items

- **Webhook listener** — `adws/adw_triggers/trigger_webhook.py` is lifted
  from tac-7 but not wired into systemd. Adding it would need a second
  unit file and a reverse proxy.
- **Dashboard** — `tac-8/app3` has a Bun+Vue real-time event dashboard
  fed by `.claude/hooks/send_event.py`. Porting it would give per-repo
  and cross-repo observability.
- **Token accounting** — the hook into claude-code JSONL parsing for
  budget.record_tokens() is a TODO; currently runs are counted but tokens
  are not yet attributed per-run.
- **Knowledge retrieval** — lessons are written to markdown but not yet
  surfaced to Leads via prompt injection. Needs a hook in the
  classify/plan slash commands.
