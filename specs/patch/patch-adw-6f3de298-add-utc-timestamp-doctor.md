# Patch: Add UTC timestamp to doctor command output line 1

## Metadata
adw_id: `6f3de298`
review_change_request: `{"number":3,"title":"Dogfood smoke: add UTC timestamp to doctor command output line 1","body":"Tiny change to verify PITER walks end-to-end after the classify_issue and plan_iso backtick fixes. In orchestrator/daemon.py --doctor output, prepend a UTC timestamp to line 1 of the output. No schema changes. No new dependencies."}`

## Issue Summary
**Original Spec:** (not provided)
**Issue:** The `--doctor` command output in `orchestrator/daemon.py` does not include a UTC timestamp on the first line of output.
**Solution:** Prepend the current UTC timestamp to the first `print` statement in `cmd_doctor()` so line 1 of the `--doctor` output begins with a UTC timestamp. Use `datetime.datetime.utcnow()` (already available via stdlib) — no new imports needed beyond `datetime`.

## Files to Modify

- `orchestrator/daemon.py` — add UTC timestamp to line 1 of `cmd_doctor()` output

## Implementation Steps

### Step 1: Add datetime import (if not already present)
- Check the existing imports at the top of `orchestrator/daemon.py` for `import datetime` or `from datetime import datetime, timezone`
- Add `import datetime` if not present (stdlib, no new dependency)

### Step 2: Prepend UTC timestamp to first print in cmd_doctor()
- In `cmd_doctor()` (line ~95), change:
  ```python
  print(f"home:          {cfg.home}")
  ```
  to:
  ```python
  ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
  print(f"[{ts}] home:          {cfg.home}")
  ```
- This prepends a UTC timestamp (ISO-8601 format) to line 1 only; all other output lines remain unchanged.

## Validation

1. Run doctor command and confirm timestamp appears on line 1:
   ```bash
   uv run orchestrator/daemon.py --doctor 2>&1 | head -1
   ```
   Expected: line starts with `[YYYY-MM-DDTHH:MM:SSZ]`

2. Confirm no other lines have timestamps added (only line 1 changes):
   ```bash
   uv run orchestrator/daemon.py --doctor 2>&1 | grep -c '^\[20'
   ```
   Expected output: `1`

## Patch Scope
**Lines of code to change:** 2–3 (one new variable assignment + modification of one print statement; possibly one import line)
**Risk level:** low
**Testing required:** Manual invocation of `--doctor` flag to confirm timestamp on line 1
