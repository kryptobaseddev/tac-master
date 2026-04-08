# tac-master

**Autonomous Tactical Agentic Coding orchestrator.** Runs as a systemd
service on a Debian 13 LXC, watches GitHub issues across a configured
allowlist of repos, and executes full PITER pipelines
(**P**lan → **I**mplement → **T**est → **E**valuate → **R**elease)
without human intervention. Self-improves by running ADWs against its own
codebase.

---

## What it does, concretely

1. You open an issue on a repo that `krypto-agent` has access to.
2. Within 20 seconds, `tac-master` detects the issue, classifies it
   (`/chore`, `/bug`, `/feature`, or `/patch`), and spawns a **Lead**
   process in an isolated git worktree.
3. The Lead generates an implementation plan, commits it, opens a PR, and
   spawns **Workers** for the remaining PITER phases.
4. Workers build the change, run tests, perform a visual review (via
   Playwright MCP), generate documentation, and — if the repo is marked
   `auto_merge: true` — ship it straight to main.
5. Every run writes a reflection lesson into `state/knowledge/` that
   future Leads can draw on.
6. Issues filed on **this** repo trigger self-improvement runs with the
   same full-auto loop.

---

## Architecture

```
          GitHub Issues (polled every 20s)
                   │
                   ▼
   ┌──────────────────────────────────────┐
   │   Orchestrator Daemon (systemd)      │
   │   - dispatcher                       │
   │   - budget enforcer                  │
   │   - repo_manager                     │
   │   - SQLite state store               │
   └──────────────┬───────────────────────┘
                  │ spawns one per qualifying issue
                  ▼
   ┌──────────────────────────────────────┐
   │   LEAD  (adw_plan_iso.py)            │
   │   - runs inside isolated worktree    │
   │   - classifies, plans, decomposes    │
   │   - spawns Workers sequentially      │
   └──────┬───────┬───────┬───────┬───────┘
          ▼       ▼       ▼       ▼
        BUILD   TEST   REVIEW  SHIP
       (adw_build_iso.py, …, adw_ship_iso.py)
```

### PITER phases

| Phase | ADW script | Purpose |
|---|---|---|
| **P**lan | `adw_plan_iso.py` | Classify issue, generate implementation plan, commit, open PR |
| **I**mplement | `adw_build_iso.py` | Execute the plan, commit code changes |
| **T**est | `adw_test_iso.py` | Run unit + E2E tests, auto-fix failures |
| **E**valuate | `adw_review_iso.py` | Playwright-based visual review, spec compliance |
| **R**elease | `adw_document_iso.py` + `adw_ship_iso.py` | Docs, final merge |

Orchestrators that chain multiple phases live in the same directory:
`adw_plan_build_iso.py`, `adw_sdlc_iso.py` (full PITER), `adw_sdlc_zte_iso.py`
(full PITER + auto-merge, "Zero Touch Engineering").

### Leads and Workers

These aren't separate agent types — they're **roles** within the existing
tac-7 ADW hierarchy:

- The **Orchestrator daemon** is a persistent Python process polling
  GitHub. One per LXC.
- A **Lead** is an `adw_plan_iso.py` invocation. One per active issue.
  Lives as a detached OS process, owns a worktree, survives across polling
  cycles, tracked in `state/tac_master.sqlite`.
- **Workers** are the phase-specific ADW scripts (`adw_build_iso.py`,
  `adw_test_iso.py`, …). Each runs in the same worktree as its Lead, under
  the Lead's process tree.

Isolation level: **full subprocess isolation** — Leads and Workers run as
separate Python processes, each spawning its own `claude` CLI session. No
shared memory, no cross-contamination.

---

## Repository layout

```
tac-master/
├── orchestrator/                    # the persistent daemon (NEW, core value-add)
│   ├── daemon.py                    # main loop + signals + logging
│   ├── dispatcher.py                # poll, classify, spawn Leads
│   ├── repo_manager.py              # clone, sync, worktree, substrate symlinks
│   ├── budget.py                    # daily + concurrent cost enforcement
│   ├── github_client.py             # REST API client (separate from in-repo gh)
│   ├── state_store.py               # SQLite schema + helpers
│   └── config.py                    # YAML + dotenv loader
├── adws/                            # [from tac-7] the ADW substrate
│   ├── adw_modules/                 # shared primitives (state, agent, github, …)
│   ├── adw_*_iso.py                 # phase scripts + orchestrators
│   ├── adw_reflect_iso.py           # NEW: self-improvement lesson writer
│   ├── adw_triggers/                # legacy cron + webhook triggers (reference only)
│   └── adw_tests/                   # self-tests
├── .claude/                         # [from tac-7] commands + hooks + settings
│   ├── commands/                    # 28 slash commands (bug, feature, plan, …)
│   ├── hooks/                       # 7 hooks (pre/post tool use, stop, …)
│   └── settings.json
├── scripts/                         # [from tac-7] utility shell scripts
├── ai_docs/                         # [from tac-7] reference docs for Claude
├── config/                          # YAML + env config (NEW)
│   ├── repos.yaml.sample            # allowlist
│   ├── budgets.yaml.sample          # cost controls
│   ├── policies.yaml.sample         # execution safety policies
│   └── identity.env.sample          # bot PAT + API keys
├── state/                           # runtime state (gitignored)
│   ├── tac_master.sqlite            # global state store
│   └── knowledge/                   # accumulated lessons (markdown)
├── repos/                           # cloned target repos (gitignored)
├── trees/                           # worktrees per active ADW (gitignored)
├── logs/                            # daemon + per-run logs (gitignored)
├── dashboard/                       # (future) multi-repo observability UI
├── deploy/
│   ├── systemd/tac-master.service   # systemd unit
│   ├── install.sh                   # Debian 13 LXC installer
│   └── README.md                    # Proxmox LXC setup guide
└── README.md
```

---

## Install

See [`deploy/README.md`](deploy/README.md) for the Proxmox LXC walkthrough.

Quick version (on any Debian 13 host):

```bash
sudo bash deploy/install.sh
sudoedit /srv/tac-master/config/identity.env       # add PAT + API key
sudoedit /srv/tac-master/config/repos.yaml         # add allowlisted repos
sudo -u krypto bash -lc 'cd /srv/tac-master && uv run orchestrator/daemon.py --doctor'
sudo systemctl start tac-master
journalctl -u tac-master -f
```

---

## Configuration

### `config/repos.yaml` — allowlist

Every repo tac-master is allowed to act on. Non-listed repos are ignored
even if the `krypto-agent` account has access. See the sample for all
fields.

### `config/budgets.yaml` — cost controls

Daily token + run caps, concurrent run limits, circuit-breaker behavior
when exceeded. Defaults: 10M tokens/day globally, 2M per repo, 15 concurrent
runs max.

### `config/policies.yaml` — execution policies

Protected branches, forbidden paths (never touch `.env`, keys, secrets),
max files/lines per PR, per-workflow rate limits. The
`self_improvement:` section governs what tac-master may do to its own repo.

### `config/identity.env` — secrets

`GITHUB_PAT`, `ANTHROPIC_API_KEY`, bot user, webhook secret. `chmod 600`.
Never commit.

---

## Triggering work

Three ways to get tac-master to act on an issue:

1. **Open a new issue** on an allowlisted repo → picked up on the next poll
   (if `new_issue` is in `triggers:`).
2. **Comment `adw`** on an existing issue → picked up on the next poll
   (if `comment_adw` is in `triggers:`).
3. **Apply a trigger label** (e.g. `tac-master`, `krypto`) → picked up on
   the next poll (if `label` is in `triggers:`).

Each trigger fires exactly once per unique issue/comment combination —
tac-master tracks processed state in `state/tac_master.sqlite`.

---

## Self-improvement loop

tac-master's own repo is just another allowlisted repo in `repos.yaml`,
marked `self: true`. When you file an issue here:

1. The daemon sees it on the next poll and dispatches a Lead.
2. The Lead clones tac-master into `repos/OWNER_tac-master/`, creates a
   worktree, and runs the `sdlc_zte` workflow (Opus model set for
   higher-quality self-modifications).
3. On success, the PR is auto-merged per user policy.
4. `policies.yaml > self_improvement > post_merge_health_check` runs a
   `--doctor` check after merge; on failure, the merge is reverted.
5. `adws/adw_reflect_iso.py` writes a lesson to `state/knowledge/` for
   future Leads to draw on.

The safety net for full autonomy is:
- **Budget caps** in `budgets.yaml`
- **Forbidden paths** in `policies.yaml` (never touch secrets, keys)
- **Post-merge health check** with automatic rollback
- **Fine-grained PAT scope** limited to allowlisted repos

---

## PITER vs. tac-7's "SDLC" terminology

tac-7's code and docs use "SDLC" (Software Development Life Cycle). The
phases are identical to PITER; the naming is a translation:

| PITER letter | tac-7 term | Script |
|---|---|---|
| **P**lan | plan | `adw_plan_iso.py` |
| **I**mplement | build | `adw_build_iso.py` |
| **T**est | test | `adw_test_iso.py` |
| **E**valuate | review | `adw_review_iso.py` |
| **R**elease | document + ship | `adw_document_iso.py`, `adw_ship_iso.py` |

The SDLC script names are kept as-is inside `adws/` to avoid divergence
from tac-7. README and docs use PITER when speaking conceptually.

---

## Lineage

Built on the layered lessons from `tac-1` through `tac-8`:

- **tac-1**: programmable primitives (`programmable/{sh,ts,py}`)
- **tac-2→tac-4**: ADW bootstrap + plan/build pipeline + hooks
- **tac-5**: modular `adw_modules/`, test phase, state piping
- **tac-6**: review + document phases, R2 uploads, Playwright MCP
- **tac-7**: full isolation (worktrees, ports, model_set), ship phase, ZTE
- **tac-8**: worked examples — task.md coordination, observability
  dashboards, Notion-driven prototyping, multi-agent patterns

tac-master lifts the full tac-7 substrate unchanged and adds the missing
three layers: **a persistent daemon, project-agnostic dispatch, and a
self-improvement loop**.

---

## License

TBD.
