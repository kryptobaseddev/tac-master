# UTC Timestamp in Doctor Command Output

**ADW ID:** 6f3de298
**Date:** 2026-04-09
**Specification:** specs/patch/patch-adw-6f3de298-add-utc-timestamp-doctor.md

## Overview

This patch adds a UTC timestamp to the first line of the `--doctor` command output in `orchestrator/daemon.py`. The timestamp is prepended in ISO-8601 format (`[YYYY-MM-DDTHH:MM:SSZ]`) to make it easy to identify when a doctor check was run without additional tooling.

## What Was Built

- UTC timestamp prepended to line 1 of `cmd_doctor()` output
- `import datetime` added to module-level imports (stdlib, no new dependency)
- Minor cleanup: split a combined inline import into two separate import statements

## Technical Implementation

### Files Modified

- `orchestrator/daemon.py`: Added `import datetime` at the top level and modified the first `print` in `cmd_doctor()` to prepend a UTC timestamp. Also split an inline `import shutil as _sh, subprocess as _sp` into two separate lines.

### Key Changes

- Added `import datetime` to module imports (line ~29).
- In `cmd_doctor()`, a `ts` variable is computed using `datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")`.
- The first `print` statement now outputs `[{ts}] home: {cfg.home}` instead of `home: {cfg.home}`.
- Only the first line of `--doctor` output is timestamped; all other lines remain unchanged.
- Renamed `original` to `_original` in the dry-run code path to avoid an unused-variable warning.

## How to Use

Run the doctor command and observe the timestamp on the first output line:

```bash
uv run orchestrator/daemon.py --doctor
```

Expected first line format:
```
[2026-04-09T12:34:56Z] home:          /path/to/home
```

To confirm only one line is timestamped:
```bash
uv run orchestrator/daemon.py --doctor 2>&1 | grep -c '^\[20'
# Expected: 1
```

## Configuration

No configuration required. The timestamp uses `datetime.timezone.utc` from the Python standard library.

## Testing

1. Run `uv run orchestrator/daemon.py --doctor 2>&1 | head -1` and verify it starts with `[YYYY-MM-DDTHH:MM:SSZ]`.
2. Run `uv run orchestrator/daemon.py --doctor 2>&1 | grep -c '^\[20'` and verify output is `1` (only line 1 has a timestamp).

## Notes

- Uses `datetime.datetime.now(datetime.timezone.utc)` (timezone-aware) which is preferred over the deprecated `datetime.datetime.utcnow()`.
- No schema changes, no new dependencies.
