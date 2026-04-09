# Patch: Add UTC timestamp to --doctor output line 1

## Metadata
adw_id: `ad2da1f7`
review_change_request: `{"number":3,"title":"Dogfood smoke: add UTC timestamp to doctor command output line 1","body":"Tiny change to verify PITER walks end-to-end after the classify_issue and plan_iso backtick fixes. In orchestrator/daemon.py --doctor output, prepend a UTC timestamp to line 1 of the output. No schema changes. No new dependencies."}`

## Issue Summary
**Original Spec:** (none provided)
**Issue:** The `--doctor` command output in `orchestrator/daemon.py` does not include a UTC timestamp on its first output line, making it harder to correlate doctor runs with logs.
**Solution:** Prepend a UTC timestamp as the very first `print()` statement in `cmd_doctor()`, using `datetime.datetime.now(datetime.timezone.utc)` — no new dependencies required.

## Files to Modify

- `orchestrator/daemon.py` — add `import datetime` to stdlib imports and insert a UTC timestamp print as the first output line of `cmd_doctor()`

## Implementation Steps

### Step 1: Add `datetime` to the existing stdlib imports
- In `orchestrator/daemon.py`, the import block at lines 28–33 already imports `argparse`, `logging`, `signal`, `sys`, `time`, and `pathlib.Path`.
- Add `import datetime` to that block (alphabetical order: after `argparse`, before `logging`).

### Step 2: Prepend UTC timestamp as first line of `cmd_doctor()` output
- In `cmd_doctor()` (starting at line 92), the current first `print` is:
  ```python
  print(f"home:          {cfg.home}")
  ```
- Insert a new line **before** it:
  ```python
  print(f"doctor: {datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')}Z")
  ```
- This produces output like: `doctor: 2026-04-09T14:32:01Z`

## Validation

1. **Syntax check** — confirm no syntax errors introduced:
   ```bash
   cd /srv/tac-master/repos/kryptobaseddev_tac-master/trees/ad2da1f7 && python -c "import ast; ast.parse(open('orchestrator/daemon.py').read()); print('OK')"
   ```

2. **Grep confirm** — verify timestamp line is present:
   ```bash
   grep -n "doctor:.*isoformat" orchestrator/daemon.py
   ```

3. **Grep confirm** — verify `import datetime` is present:
   ```bash
   grep -n "^import datetime" orchestrator/daemon.py
   ```

## Patch Scope
**Lines of code to change:** 2 (1 new import line + 1 new print line)
**Risk level:** low
**Testing required:** Syntax check; no unit tests affected; no schema changes; no new dependencies
