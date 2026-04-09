# Patch: Add UTC timestamp to doctor command output line 1

## Metadata
adw_id: `d5ab7f05`
review_change_request: `{"number":3,"title":"Dogfood smoke: add UTC timestamp to doctor command output line 1","body":"Tiny change to verify PITER walks end-to-end after the classify_issue and plan_iso backtick fixes. In orchestrator/daemon.py --doctor output, prepend a UTC timestamp to line 1 of the output. No schema changes. No new dependencies."}`

## Issue Summary
**Original Spec:** (none provided)
**Issue:** The `--doctor` command output in `orchestrator/daemon.py` does not include a UTC timestamp, making it harder to correlate runs with logs or other timestamped output.
**Solution:** Prepend a UTC timestamp as the very first line printed by `cmd_doctor()` using `datetime.datetime.utcnow()` (already available in stdlib — no new dependencies).

## Files to Modify

- `orchestrator/daemon.py` — add UTC timestamp as first `print` in `cmd_doctor()`

## Implementation Steps
IMPORTANT: Execute every step in order, top to bottom.

### Step 1: Add datetime import (if not already present)
- Check the existing imports at the top of `orchestrator/daemon.py`
- If `import datetime` is not already imported, add it alongside the existing stdlib imports

### Step 2: Prepend UTC timestamp to line 1 of `cmd_doctor` output
- In `cmd_doctor()` (line 92), insert a new `print` statement as the **first** line of the function body (before the existing `print(f"home: ...")` on line 95)
- Use: `print(f"doctor: {datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')}")`
- This makes the UTC timestamp the first line of doctor output

## Validation
Execute every command to validate the patch is complete with zero regressions.

1. **Syntax check** — confirm no syntax errors introduced:
   ```
   cd /srv/tac-master/repos/kryptobaseddev_tac-master/trees/d5ab7f05 && uv run python -m py_compile orchestrator/daemon.py
   ```

2. **Smoke run** — verify the timestamp appears as line 1:
   ```
   cd /srv/tac-master/repos/kryptobaseddev_tac-master/trees/d5ab7f05 && uv run orchestrator/daemon.py --doctor 2>&1 | head -1
   ```
   Expected output should match pattern: `doctor: 20\d\d-\d\d-\d\dT\d\d:\d\d:\d\dZ`

## Patch Scope
**Lines of code to change:** 2 (1 import line if needed + 1 print statement)
**Risk level:** low
**Testing required:** Python syntax check + manual `--doctor` invocation to confirm timestamp on line 1
