# Patch: Add UTC timestamp to doctor command output line 1

## Metadata
adw_id: `4d4917bf`
review_change_request: `{"number":3,"title":"Dogfood smoke: add UTC timestamp to doctor command output line 1","body":"Tiny change to verify PITER walks end-to-end after the classify_issue and plan_iso backtick fixes. In orchestrator/daemon.py --doctor output, prepend a UTC timestamp to line 1 of the output. No schema changes. No new dependencies."}`

## Issue Summary
**Original Spec:** (none provided)
**Issue:** The `--doctor` command output in `orchestrator/daemon.py` does not include a UTC timestamp on its first output line.
**Solution:** Prepend a UTC timestamp as the first printed line inside `cmd_doctor()` using `datetime.datetime.utcnow()` (already available via stdlib — no new dependencies needed).

## Files to Modify

- `orchestrator/daemon.py` — add `import datetime` to imports and prepend timestamp print to `cmd_doctor()`

## Implementation Steps
IMPORTANT: Execute every step in order, top to bottom.

### Step 1: Add `datetime` import
- In `orchestrator/daemon.py`, add `import datetime` to the stdlib imports block (lines 28–33), immediately after `import argparse`.

### Step 2: Prepend UTC timestamp as first line of `cmd_doctor`
- In `cmd_doctor()` (line 92), insert a new print statement as the very first line of the function body (before the `home:` print at line 95):
  ```python
  print(f"doctor UTC:    {datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')}")
  ```

## Validation
Execute every command to validate the patch is complete with zero regressions.

1. **Syntax check** — verify no import or syntax errors:
   ```
   cd /srv/tac-master/repos/kryptobaseddev_tac-master/trees/4d4917bf && uv run python -m py_compile orchestrator/daemon.py
   ```

2. **Smoke-run doctor** — confirm UTC timestamp appears as first output line:
   ```
   cd /srv/tac-master/repos/kryptobaseddev_tac-master/trees/4d4917bf && uv run orchestrator/daemon.py --doctor 2>/dev/null | head -1
   ```
   Expected output starts with: `doctor UTC:    2026-`

## Patch Scope
**Lines of code to change:** 2 (one import line, one print statement)
**Risk level:** low
**Testing required:** Python syntax check + manual `--doctor` smoke run
