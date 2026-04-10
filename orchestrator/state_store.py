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
from typing import Iterator

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
    cleo_task_id   TEXT,           -- CLEO task ID if dispatched from a CLEO task (e.g., "T084")
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

SCHEMA_V2 = """
-- v2_orchestrator.sql
-- tac-master: Orchestrator session persistence schema (T066)
--
-- Migration strategy: additive-only, idempotent.
-- Safe to run on an existing tac_master.sqlite — no existing tables are altered.
--
-- SQLite pragmas (set at connection time, repeated here for clarity):
--   PRAGMA journal_mode = WAL;
--   PRAGMA synchronous = NORMAL;
--   PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- schema_version — tracks applied migrations
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    description TEXT    NOT NULL,
    applied_at  INTEGER NOT NULL   -- Unix epoch seconds
);

-- ---------------------------------------------------------------------------
-- orchestrator_agents — persistent orchestrator session state
--
-- One row per orchestrator session (active or archived).
-- status:  idle | executing | waiting | blocked | complete
-- archived: 0 = active, 1 = soft-deleted / historical
-- metadata: JSON text — {"model": "...", "tools": [...], "slash_commands": [...]}
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS orchestrator_agents (
    id              TEXT    PRIMARY KEY,               -- 'oa-<uuid4>'
    session_id      TEXT,                              -- Claude SDK --session value
    system_prompt   TEXT,                              -- full system prompt at creation
    status          TEXT    NOT NULL DEFAULT 'idle',   -- idle|executing|waiting|blocked|complete
    working_dir     TEXT,                              -- CWD at time of spawn
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    total_cost      REAL    NOT NULL DEFAULT 0.0,      -- USD, accumulated
    archived        INTEGER NOT NULL DEFAULT 0,        -- 0=active, 1=archived
    metadata        TEXT    NOT NULL DEFAULT '{}',     -- JSON: model, tools, capabilities
    created_at      INTEGER NOT NULL,                  -- Unix epoch seconds
    updated_at      INTEGER NOT NULL                   -- Unix epoch seconds
);

CREATE INDEX IF NOT EXISTS idx_orch_agents_status   ON orchestrator_agents(status);
CREATE INDEX IF NOT EXISTS idx_orch_agents_session  ON orchestrator_agents(session_id);
CREATE INDEX IF NOT EXISTS idx_orch_agents_archived ON orchestrator_agents(archived);

-- ---------------------------------------------------------------------------
-- agent_instances — worker agent lifecycle tracking
--
-- One row per agent invocation within an orchestrator session.
-- adw_id bridges to the existing runs table (cross-table join key).
-- adw_step stores the PITER phase string (classify_iso, plan_iso, ...).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_instances (
    id                    TEXT    PRIMARY KEY,             -- 'ai-<uuid4>'
    orchestrator_agent_id TEXT    NOT NULL,                -- FK → orchestrator_agents.id
    name                  TEXT    NOT NULL,                -- human label, e.g. 'sdlc_implementor'
    model                 TEXT    NOT NULL,                -- e.g. 'claude-sonnet-4-5'
    system_prompt         TEXT,
    working_dir           TEXT,
    git_worktree          TEXT,                            -- worktree path if applicable
    status                TEXT    NOT NULL DEFAULT 'idle', -- idle|executing|waiting|blocked|complete
    session_id            TEXT,                            -- Claude SDK session for resumption
    adw_id                TEXT,                            -- links to runs.adw_id
    adw_step              TEXT,                            -- PITER phase
    input_tokens          INTEGER NOT NULL DEFAULT 0,
    output_tokens         INTEGER NOT NULL DEFAULT 0,
    total_cost            REAL    NOT NULL DEFAULT 0.0,
    archived              INTEGER NOT NULL DEFAULT 0,
    metadata              TEXT    NOT NULL DEFAULT '{}',   -- JSON: extra context
    created_at            INTEGER NOT NULL,                -- Unix epoch seconds
    updated_at            INTEGER NOT NULL,                -- Unix epoch seconds
    FOREIGN KEY(orchestrator_agent_id) REFERENCES orchestrator_agents(id)
);

CREATE INDEX IF NOT EXISTS idx_agent_inst_orch    ON agent_instances(orchestrator_agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_inst_status  ON agent_instances(status);
CREATE INDEX IF NOT EXISTS idx_agent_inst_adw     ON agent_instances(adw_id);
CREATE INDEX IF NOT EXISTS idx_agent_inst_session ON agent_instances(session_id);

-- ---------------------------------------------------------------------------
-- chat_messages — full conversation history for session resumption
--
-- sender_type / receiver_type: user | orchestrator | agent
-- metadata JSON: {"input_tokens": N, "output_tokens": N, "cost_usd": N, "model": "..."}
-- summary is nullable — populated asynchronously by the AI summarizer.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chat_messages (
    id                    TEXT    PRIMARY KEY,         -- 'cm-<uuid4>'
    orchestrator_agent_id TEXT    NOT NULL,            -- FK → orchestrator_agents.id
    sender_type           TEXT    NOT NULL,            -- user | orchestrator | agent
    receiver_type         TEXT    NOT NULL,            -- user | orchestrator | agent
    message               TEXT    NOT NULL,            -- full text content
    summary               TEXT,                        -- AI-generated summary (nullable)
    agent_id              TEXT,                        -- FK → agent_instances.id (null if not agent-originated)
    session_id            TEXT,                        -- Claude SDK session ID at time of message
    metadata              TEXT    NOT NULL DEFAULT '{}', -- JSON: cost, tokens, model
    created_at            INTEGER NOT NULL,            -- Unix epoch seconds
    updated_at            INTEGER NOT NULL,            -- Unix epoch seconds
    FOREIGN KEY(orchestrator_agent_id) REFERENCES orchestrator_agents(id),
    FOREIGN KEY(agent_id)              REFERENCES agent_instances(id)
);

CREATE INDEX IF NOT EXISTS idx_chat_orch     ON chat_messages(orchestrator_agent_id);
CREATE INDEX IF NOT EXISTS idx_chat_sender   ON chat_messages(sender_type);
CREATE INDEX IF NOT EXISTS idx_chat_receiver ON chat_messages(receiver_type);
CREATE INDEX IF NOT EXISTS idx_chat_session  ON chat_messages(session_id);
CREATE INDEX IF NOT EXISTS idx_chat_ts       ON chat_messages(created_at);

-- ---------------------------------------------------------------------------
-- system_logs — thinking blocks, tool use blocks, and app-level events
--
-- log_type:  thinking | tool_use | hook | response | app
-- level:     DEBUG | INFO | WARNING | ERROR
-- payload:   JSON — full structured data (ThinkingBlock, ToolUseBlock, etc.)
-- content:   human-readable text extracted from payload for display
-- entry_index: position within a conversation turn (ordering multi-block responses)
-- App-level logs (log_type='app') have orchestrator_agent_id=NULL, agent_id=NULL.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS system_logs (
    id                    TEXT    PRIMARY KEY,             -- 'sl-<uuid4>'
    orchestrator_agent_id TEXT,                           -- FK → orchestrator_agents.id (nullable)
    agent_id              TEXT,                           -- FK → agent_instances.id (nullable)
    session_id            TEXT,                           -- Claude SDK session
    adw_id                TEXT,                           -- links to runs.adw_id
    adw_step              TEXT,                           -- PITER phase
    level                 TEXT    NOT NULL DEFAULT 'INFO', -- DEBUG|INFO|WARNING|ERROR
    log_type              TEXT    NOT NULL,                -- thinking|tool_use|hook|response|app
    event_type            TEXT,                           -- specific event (PreToolUse, Stop, etc.)
    content               TEXT,                           -- primary text content
    payload               TEXT    NOT NULL DEFAULT '{}',  -- JSON: full structured data
    summary               TEXT,                           -- AI-generated summary (nullable)
    entry_index           INTEGER,                        -- position in conversation turn
    timestamp             INTEGER NOT NULL,               -- Unix epoch seconds
    FOREIGN KEY(orchestrator_agent_id) REFERENCES orchestrator_agents(id),
    FOREIGN KEY(agent_id)              REFERENCES agent_instances(id)
);

CREATE INDEX IF NOT EXISTS idx_syslog_orch    ON system_logs(orchestrator_agent_id);
CREATE INDEX IF NOT EXISTS idx_syslog_agent   ON system_logs(agent_id);
CREATE INDEX IF NOT EXISTS idx_syslog_session ON system_logs(session_id);
CREATE INDEX IF NOT EXISTS idx_syslog_adw     ON system_logs(adw_id);
CREATE INDEX IF NOT EXISTS idx_syslog_type    ON system_logs(log_type);
CREATE INDEX IF NOT EXISTS idx_syslog_level   ON system_logs(level);
CREATE INDEX IF NOT EXISTS idx_syslog_ts      ON system_logs(timestamp);

-- ---------------------------------------------------------------------------
-- Initial schema_version entry (version 2 = v2 orchestrator tables)
-- INSERT OR IGNORE ensures idempotency on repeated runs.
-- ---------------------------------------------------------------------------
INSERT OR IGNORE INTO schema_version (version, description, applied_at)
VALUES (2, 'Add orchestrator_agents, agent_instances, chat_messages, system_logs', strftime('%s', 'now'));
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
            c.executescript(SCHEMA_V2)
            # T110 migration: add cleo_task_id to agent_instances for CLEO→GH issue mapping
            try:
                c.execute(
                    "ALTER TABLE agent_instances ADD COLUMN cleo_task_id TEXT"
                )
            except sqlite3.OperationalError:
                pass  # column already exists

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
                   workflow: str, model_set: str, cleo_task_id: str | None = None) -> None:
        with self.conn() as c:
            c.execute(
                """INSERT INTO runs (adw_id, repo_url, issue_number, workflow, model_set,
                   cleo_task_id, status, started_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)""",
                (adw_id, repo_url, issue_number, workflow, model_set, cleo_task_id, int(time.time())),
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

    def get_cleo_task_id(self, adw_id: str) -> str | None:
        """Returns the CLEO task ID for a given adw_id, or None if not set."""
        with self.conn() as c:
            row = c.execute(
                "SELECT cleo_task_id FROM runs WHERE adw_id=?",
                (adw_id,),
            ).fetchone()
            return row["cleo_task_id"] if row else None

    def set_agent_instance_cleo_task_id(
        self, adw_id: str, cleo_task_id: str
    ) -> None:
        """Store cleo_task_id on the agent_instances row(s) for a given adw_id.

        Called after create_issue_from_task creates the GitHub issue so the
        CLEO task ID → issue mapping is persisted alongside the run.
        """
        with self.conn() as c:
            c.execute(
                "UPDATE agent_instances SET cleo_task_id=? WHERE adw_id=?",
                (cleo_task_id, adw_id),
            )

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
