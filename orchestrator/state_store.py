"""SQLite state store for tac-master.

Global state across all repos, runs, and workers. Separate from the
per-ADW adw_state.json files used inside each worktree (those remain
ADW-internal and are never touched by the orchestrator except via the
state path for reading final results).

Schema:
  repos          — allowlisted repositories
  issues         — issues seen per repo + processing status
  runs           — Lead processes (one per dispatched issue)
  phases         — Worker processes (one per ADW phase within a run)
  events         — append-only event log (used by dashboard + reflection)
  budget_usage   — rolling token/run counters per repo
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS repos (
    url            TEXT PRIMARY KEY,
    slug           TEXT NOT NULL,
    is_self        INTEGER NOT NULL DEFAULT 0,
    default_workflow TEXT NOT NULL,
    model_set      TEXT NOT NULL,
    auto_merge     INTEGER NOT NULL DEFAULT 0,
    added_at       INTEGER NOT NULL,
    last_polled_at INTEGER
);

CREATE TABLE IF NOT EXISTS issues (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_url       TEXT NOT NULL,
    issue_number   INTEGER NOT NULL,
    title          TEXT,
    status         TEXT NOT NULL,  -- seen | queued | dispatched | completed | failed | skipped
    first_seen_at  INTEGER NOT NULL,
    last_updated_at INTEGER NOT NULL,
    last_comment_id INTEGER,
    UNIQUE(repo_url, issue_number),
    FOREIGN KEY(repo_url) REFERENCES repos(url)
);
CREATE INDEX IF NOT EXISTS idx_issues_status ON issues(status);
CREATE INDEX IF NOT EXISTS idx_issues_repo ON issues(repo_url);

CREATE TABLE IF NOT EXISTS runs (
    adw_id         TEXT PRIMARY KEY,
    repo_url       TEXT NOT NULL,
    issue_number   INTEGER NOT NULL,
    workflow       TEXT NOT NULL,
    model_set      TEXT NOT NULL,
    worktree_path  TEXT,
    status         TEXT NOT NULL,  -- pending | running | succeeded | failed | aborted
    started_at     INTEGER,
    ended_at       INTEGER,
    pid            INTEGER,        -- OS pid of the Lead process
    exit_code      INTEGER,
    tokens_used    INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(repo_url) REFERENCES repos(url)
);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_repo ON runs(repo_url);

CREATE TABLE IF NOT EXISTS phases (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    adw_id         TEXT NOT NULL,
    phase          TEXT NOT NULL,  -- plan | build | test | review | document | ship | reflect
    status         TEXT NOT NULL,  -- running | succeeded | failed
    started_at     INTEGER NOT NULL,
    ended_at       INTEGER,
    tokens_used    INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(adw_id) REFERENCES runs(adw_id)
);
CREATE INDEX IF NOT EXISTS idx_phases_adw ON phases(adw_id);

CREATE TABLE IF NOT EXISTS events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             INTEGER NOT NULL,
    repo_url       TEXT,
    adw_id         TEXT,
    kind           TEXT NOT NULL,  -- dispatch | phase_start | phase_end | error | budget | knowledge | webhook
    payload        TEXT            -- JSON blob
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);

CREATE TABLE IF NOT EXISTS budget_usage (
    day            TEXT NOT NULL,   -- YYYY-MM-DD
    repo_url       TEXT NOT NULL,   -- "__global__" for the global row
    tokens_used    INTEGER NOT NULL DEFAULT 0,
    runs_count     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, repo_url)
);
"""


class StateStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._init_schema()

    def _init_schema(self) -> None:
        with self.conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        if self._conn is None:
            self._conn = sqlite3.connect(self.path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON;")
            self._conn.execute("PRAGMA journal_mode = WAL;")
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ----- repos -----

    def upsert_repo(self, url: str, slug: str, is_self: bool, workflow: str,
                    model_set: str, auto_merge: bool) -> None:
        with self.conn() as c:
            c.execute(
                """INSERT INTO repos (url, slug, is_self, default_workflow, model_set,
                   auto_merge, added_at) VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(url) DO UPDATE SET
                     slug=excluded.slug,
                     is_self=excluded.is_self,
                     default_workflow=excluded.default_workflow,
                     model_set=excluded.model_set,
                     auto_merge=excluded.auto_merge""",
                (url, slug, int(is_self), workflow, model_set, int(auto_merge), int(time.time())),
            )

    def mark_polled(self, url: str) -> None:
        with self.conn() as c:
            c.execute("UPDATE repos SET last_polled_at = ? WHERE url = ?",
                      (int(time.time()), url))

    # ----- issues -----

    def seen_issue(self, repo_url: str, issue_number: int, title: str,
                   last_comment_id: int | None) -> str:
        """Returns current status of the issue after recording it."""
        now = int(time.time())
        with self.conn() as c:
            row = c.execute(
                "SELECT status, last_comment_id FROM issues WHERE repo_url=? AND issue_number=?",
                (repo_url, issue_number),
            ).fetchone()
            if row is None:
                c.execute(
                    """INSERT INTO issues (repo_url, issue_number, title, status,
                       first_seen_at, last_updated_at, last_comment_id)
                       VALUES (?, ?, ?, 'seen', ?, ?, ?)""",
                    (repo_url, issue_number, title, now, now, last_comment_id),
                )
                return "seen"
            else:
                c.execute(
                    """UPDATE issues SET title=?, last_updated_at=?, last_comment_id=?
                       WHERE repo_url=? AND issue_number=?""",
                    (title, now, last_comment_id, repo_url, issue_number),
                )
                return row["status"]

    def set_issue_status(self, repo_url: str, issue_number: int, status: str) -> None:
        with self.conn() as c:
            c.execute(
                "UPDATE issues SET status=?, last_updated_at=? WHERE repo_url=? AND issue_number=?",
                (status, int(time.time()), repo_url, issue_number),
            )

    # ----- runs -----

    def create_run(self, adw_id: str, repo_url: str, issue_number: int,
                   workflow: str, model_set: str) -> None:
        with self.conn() as c:
            c.execute(
                """INSERT INTO runs (adw_id, repo_url, issue_number, workflow, model_set,
                   status, started_at) VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
                (adw_id, repo_url, issue_number, workflow, model_set, int(time.time())),
            )

    def update_run(self, adw_id: str, **kwargs) -> None:
        if not kwargs:
            return
        keys = ", ".join(f"{k}=?" for k in kwargs)
        with self.conn() as c:
            c.execute(f"UPDATE runs SET {keys} WHERE adw_id=?",
                      (*kwargs.values(), adw_id))

    def active_runs_count(self, repo_url: str | None = None) -> int:
        with self.conn() as c:
            if repo_url:
                row = c.execute(
                    "SELECT COUNT(*) AS n FROM runs WHERE status IN ('pending', 'running') AND repo_url=?",
                    (repo_url,),
                ).fetchone()
            else:
                row = c.execute(
                    "SELECT COUNT(*) AS n FROM runs WHERE status IN ('pending', 'running')"
                ).fetchone()
            return row["n"] if row else 0

    def list_active_runs(self) -> list[dict]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT * FROM runs WHERE status IN ('pending', 'running')"
            ).fetchall()
            return [dict(r) for r in rows]

    # ----- phases -----

    def start_phase(self, adw_id: str, phase: str) -> int:
        with self.conn() as c:
            cur = c.execute(
                """INSERT INTO phases (adw_id, phase, status, started_at)
                   VALUES (?, ?, 'running', ?)""",
                (adw_id, phase, int(time.time())),
            )
            return cur.lastrowid or 0

    def end_phase(self, phase_id: int, status: str, tokens_used: int = 0) -> None:
        with self.conn() as c:
            c.execute(
                "UPDATE phases SET status=?, ended_at=?, tokens_used=? WHERE id=?",
                (status, int(time.time()), tokens_used, phase_id),
            )

    # ----- events -----

    def record_event(self, kind: str, payload: str = "{}",
                     repo_url: str | None = None, adw_id: str | None = None) -> None:
        with self.conn() as c:
            c.execute(
                "INSERT INTO events (ts, repo_url, adw_id, kind, payload) VALUES (?, ?, ?, ?, ?)",
                (int(time.time()), repo_url, adw_id, kind, payload),
            )

    # ----- budget -----

    def add_tokens(self, repo_url: str, tokens: int) -> None:
        day = time.strftime("%Y-%m-%d")
        with self.conn() as c:
            for key in (repo_url, "__global__"):
                c.execute(
                    """INSERT INTO budget_usage (day, repo_url, tokens_used, runs_count)
                       VALUES (?, ?, ?, 0)
                       ON CONFLICT(day, repo_url) DO UPDATE SET
                         tokens_used = tokens_used + excluded.tokens_used""",
                    (day, key, tokens),
                )

    def add_run_count(self, repo_url: str, n: int = 1) -> None:
        day = time.strftime("%Y-%m-%d")
        with self.conn() as c:
            for key in (repo_url, "__global__"):
                c.execute(
                    """INSERT INTO budget_usage (day, repo_url, tokens_used, runs_count)
                       VALUES (?, ?, 0, ?)
                       ON CONFLICT(day, repo_url) DO UPDATE SET
                         runs_count = runs_count + excluded.runs_count""",
                    (day, key, n),
                )

    def usage_today(self, repo_url: str) -> tuple[int, int]:
        day = time.strftime("%Y-%m-%d")
        with self.conn() as c:
            row = c.execute(
                "SELECT tokens_used, runs_count FROM budget_usage WHERE day=? AND repo_url=?",
                (day, repo_url),
            ).fetchone()
            return (row["tokens_used"], row["runs_count"]) if row else (0, 0)
