# Idempotent Config Implementation

**Date**: 2026-04-09
**Commit**: 73d0df0

## Changes Made

### 1. .gitignore — untrack real config files

Added `config/repos.yaml`, `config/budgets.yaml`, and `config/policies.yaml` to
`.gitignore`. Only `*.sample` templates remain tracked. Future `git pull` / `git reset
--hard` operations will never touch user config files.

### 2. git rm --cached — removed from index on both sides

Ran `git rm --cached` locally (dev machine) and manually on the LXC so the index
matches the new gitignore. Files remain on disk unchanged.

### 3. config/repos.yaml + config/budgets.yaml — OWNER replaced

All active (uncommented) repo URLs now use `kryptobaseddev` instead of `OWNER`.
The daemon no longer fails at startup with FATAL placeholder warnings.

### 4. scripts/tac-update.sh — config protection wrappers

Added `_protect_configs` and `_restore_configs` shell functions:

- `_protect_configs` copies `repos.yaml`, `budgets.yaml`, `policies.yaml` to
  `*.user-backup` before `git reset --hard`.
- `_restore_configs` moves the backups back after the pull.
- On first install (no existing config, no backup), copies from the `.sample`
  template and prints a warning to fill in real values.

Both are called inside `update_tac_master()` to bracket the git operations.

### 5. orchestrator/config_validator.py — new module

Read-only validator for repos.yaml and budgets.yaml. Returns `list[str]` of
warnings tagged `FATAL:` (blocks startup) or `warning:` (logged only). Checks:

- Required fields present and correct type
- No OWNER placeholder in any active repo URL
- `global.max_tokens_per_day` present in budgets.yaml
- Non-empty repos list

Entry points: `validate_repos_yaml(path)`, `validate_budgets_yaml(path)`,
`validate_all(config_dir)`, `has_fatal(warnings)`.

### 6. orchestrator/daemon.py — validation at startup

Calls `validate_all(cfg.home / "config")` before any subsystem is initialised.
FATAL warnings are logged at ERROR level; if any are present the daemon exits with
code 3. Non-fatal warnings are logged at WARNING level.

### 7. dashboard/server/src/index.ts — GET /api/config/validate

New read-only endpoint. Reads both config files via the existing `getReposConfig()`
and `getBudgetsConfig()` helpers and returns:

```json
{ "valid": true, "warnings": [] }
```

or a list of human-readable warning strings when issues are found.

## Deployment

1. Pushed commit `73d0df0` to `kryptobaseddev/tac-master` (main).
2. On LXC (`10.0.10.22`): moved user configs aside, `git pull`, restored configs,
   popped stash.
3. `systemctl restart tac-master` — daemon active.
4. `uv run orchestrator/daemon.py --doctor` — all checks passed, no OWNER warnings,
   2 repos listed (`kryptobaseddev/tac-master`, `kryptobaseddev/caamp`).
