#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "pyyaml>=6.0",
#   "python-dotenv>=1.0",
# ]
# ///

"""
Operational helpers for tac-master — non-dispatch lifecycle actions.

Usable as a library (imported by the dashboard server caller or other
orchestrator code) AND as a CLI tool:

    uv run orchestrator/ops.py retry 42 https://github.com/owner/repo
    uv run orchestrator/ops.py status 42 https://github.com/owner/repo

@task T012
@epic T004
@why Reduces operator toil — failed issues currently require manual SQLite
     editing to retry; this module provides a single guarded code path for
     both CLI and dashboard surfaces.
@what Exposes retry_issue() which validates issue status, clears it to
     'pending', and records an ops event so the next poll cycle re-dispatches.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Statuses that are safe to retry (issue must be in one of these to proceed)
_RETRYABLE_STATUSES = {"failed", "aborted"}

# Statuses that are already active — retrying would double-dispatch
_ACTIVE_STATUSES = {"pending", "dispatched", "running"}


@dataclass
class RetryResult:
    """
    @task T012
    @epic T004
    @why Typed return value lets callers distinguish guard failures from errors
    @what Carries success flag, previous status, and a human-readable message
    """
    ok: bool
    message: str
    previous_status: Optional[str] = None
    issue_number: Optional[int] = None
    repo_url: Optional[str] = None


def retry_issue(
    issue_number: int,
    repo_url: str,
    db_path: Optional[str] = None,
) -> RetryResult:
    """
    Clear a failed/aborted issue back to 'pending' so the next poll cycle
    re-dispatches it.

    Guards:
    - Refuses if the issue is already active (pending / dispatched / running).
    - Refuses if the issue is not found in the database.
    - Refuses if the issue status is not in _RETRYABLE_STATUSES.

    The operation updates both the ``issues`` status column AND records an
    ``ops_retry`` event in the events table for audit purposes.

    @task T012
    @epic T004
    @why Shared service method used by both CLI and dashboard endpoint to
         ensure guards are applied consistently on every retry path.
    @what Validates issue status then performs atomic SQLite update + event log.

    Args:
        issue_number: GitHub issue number to retry.
        repo_url:     Full GitHub repo URL (e.g. https://github.com/owner/repo).
        db_path:      Override path to tac_master.sqlite.  Defaults to the
                      canonical location relative to TAC_MASTER_HOME env var.

    Returns:
        RetryResult with ok=True on success, ok=False with a descriptive
        message on any guard failure or error.
    """
    import sqlite3

    path = _resolve_db_path(db_path)
    if not path.exists():
        return RetryResult(
            ok=False,
            message=f"Database not found at {path}",
            issue_number=issue_number,
            repo_url=repo_url,
        )

    try:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        try:
            return _do_retry(conn, issue_number, repo_url)
        finally:
            conn.close()
    except Exception as exc:
        log.exception("retry_issue failed for %s#%d: %s", repo_url, issue_number, exc)
        return RetryResult(
            ok=False,
            message=f"Unexpected error: {exc}",
            issue_number=issue_number,
            repo_url=repo_url,
        )


def _do_retry(conn, issue_number: int, repo_url: str) -> RetryResult:
    """Perform the guarded status update inside an open connection."""
    row = conn.execute(
        "SELECT status FROM issues WHERE repo_url=? AND issue_number=?",
        (repo_url, issue_number),
    ).fetchone()

    if row is None:
        return RetryResult(
            ok=False,
            message=(
                f"Issue #{issue_number} not found in database for repo {repo_url}. "
                "It may not have been seen by the poller yet."
            ),
            issue_number=issue_number,
            repo_url=repo_url,
        )

    current_status: str = row["status"]

    # Guard: already active — retrying would cause a duplicate dispatch
    if current_status in _ACTIVE_STATUSES:
        return RetryResult(
            ok=False,
            message=(
                f"Issue #{issue_number} is currently '{current_status}' — "
                "retry is not allowed while a run is active."
            ),
            previous_status=current_status,
            issue_number=issue_number,
            repo_url=repo_url,
        )

    # Guard: not in a retryable terminal state
    if current_status not in _RETRYABLE_STATUSES:
        return RetryResult(
            ok=False,
            message=(
                f"Issue #{issue_number} has status '{current_status}' which is not "
                f"retryable (must be one of: {', '.join(sorted(_RETRYABLE_STATUSES))})."
            ),
            previous_status=current_status,
            issue_number=issue_number,
            repo_url=repo_url,
        )

    now = int(time.time())

    # Atomic: clear issue status to 'seen' so _should_dispatch() re-qualifies it
    # on the next poll.  We use 'seen' (not 'pending') because 'pending' is a
    # run-level status — the issue table uses 'seen' to mean "not yet handled".
    conn.execute(
        "UPDATE issues SET status='seen', last_updated_at=? WHERE repo_url=? AND issue_number=?",
        (now, repo_url, issue_number),
    )

    # Audit trail
    conn.execute(
        "INSERT INTO events (ts, repo_url, adw_id, kind, payload) VALUES (?, ?, NULL, ?, ?)",
        (
            now,
            repo_url,
            "ops_retry",
            json.dumps({
                "issue_number": issue_number,
                "repo_url": repo_url,
                "previous_status": current_status,
                "actor": "ops.retry_issue",
            }),
        ),
    )
    conn.commit()

    log.info(
        "ops.retry_issue: reset issue %s#%d from '%s' → 'seen'; "
        "next poll cycle will re-dispatch.",
        repo_url, issue_number, current_status,
    )

    return RetryResult(
        ok=True,
        message=(
            f"Issue #{issue_number} reset from '{current_status}' to 'seen'. "
            "The next poll cycle will re-dispatch it."
        ),
        previous_status=current_status,
        issue_number=issue_number,
        repo_url=repo_url,
    )


def get_issue_status(
    issue_number: int,
    repo_url: str,
    db_path: Optional[str] = None,
) -> Optional[str]:
    """
    Return the current status string for an issue, or None if not found.

    @task T012
    @epic T004
    @why Allows CLI and dashboard to query issue status without a full retry
    @what Simple read-only SQLite lookup on the issues table
    """
    import sqlite3

    path = _resolve_db_path(db_path)
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT status FROM issues WHERE repo_url=? AND issue_number=?",
            (repo_url, issue_number),
        ).fetchone()
        conn.close()
        return row["status"] if row else None
    except Exception:
        return None


def _resolve_db_path(override: Optional[str]) -> Path:
    """Resolve the path to tac_master.sqlite."""
    if override:
        return Path(override)
    home = os.environ.get("TAC_MASTER_HOME", "/srv/tac-master")
    return Path(home) / "state" / "tac_master.sqlite"


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def _cli() -> None:
    """
    Command-line interface for ops.py.

    @task T012
    @epic T004
    @why Gives operators a direct CLI surface without needing to edit SQLite
    @what Parses argv, calls retry_issue() or get_issue_status(), exits 0/1
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="tac-ops",
        description="tac-master operational utilities",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # retry sub-command
    retry_p = sub.add_parser(
        "retry",
        help="Reset a failed/aborted issue so it re-dispatches on next poll",
    )
    retry_p.add_argument("issue_number", type=int, help="GitHub issue number")
    retry_p.add_argument("repo_url", help="Full GitHub repo URL (https://github.com/owner/repo)")
    retry_p.add_argument("--db", default=None, help="Path to tac_master.sqlite (optional override)")

    # status sub-command
    status_p = sub.add_parser(
        "status",
        help="Print current issue status from the state store",
    )
    status_p.add_argument("issue_number", type=int, help="GitHub issue number")
    status_p.add_argument("repo_url", help="Full GitHub repo URL")
    status_p.add_argument("--db", default=None, help="Path to tac_master.sqlite (optional override)")

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if args.cmd == "retry":
        result = retry_issue(args.issue_number, args.repo_url, db_path=args.db)
        if result.ok:
            print(f"OK: {result.message}")
            sys.exit(0)
        else:
            print(f"ERROR: {result.message}", file=sys.stderr)
            sys.exit(1)

    elif args.cmd == "status":
        status = get_issue_status(args.issue_number, args.repo_url, db_path=args.db)
        if status is None:
            print(f"NOT FOUND: issue #{args.issue_number} not in database for {args.repo_url}",
                  file=sys.stderr)
            sys.exit(1)
        print(f"status: {status}")
        sys.exit(0)


if __name__ == "__main__":
    _cli()
