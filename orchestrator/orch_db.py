"""SQLite CRUD adapter for OrchestratorService.

Provides 8 core database operations for orchestrator persistence:
- create_orchestrator, get_orchestrator, update_orchestrator_session, update_orchestrator_costs
- insert_chat_message, get_chat_history
- insert_system_log, get_turn_count

Uses stdlib sqlite3 with parameterized queries (? placeholders).
WAL mode enabled for concurrent access (daemon + orchestrator processes).

This adapter is self-contained for T069 but structured to defer to T103's
db_repositories.py repository layer when available (T103 Wave 2).
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional


class OrchDBAdapter:
    """CRUD operations for orchestrator_agents, chat_messages, and system_logs tables."""

    def __init__(self, db_path: Path):
        """Initialize adapter with SQLite database path.

        Args:
            db_path: Path to tac_master.sqlite
        """
        self.db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None
        self._ensure_wal_mode()

    def _ensure_wal_mode(self) -> None:
        """Enable WAL mode on connection to allow concurrent reads during writes."""
        with self.conn() as c:
            c.execute("PRAGMA journal_mode = WAL;")
            c.execute("PRAGMA synchronous = NORMAL;")
            c.execute("PRAGMA foreign_keys = ON;")

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        """Context manager for database connections.

        Yields:
            sqlite3.Connection with row_factory set to Row for dict-like access
        """
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
            # Ensure pragmas on connection reuse
            self._conn.execute("PRAGMA foreign_keys = ON;")
            self._conn.execute("PRAGMA journal_mode = WAL;")
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ─────────────────────────────────────────────────────────────────────
    # orchestrator_agents CRUD
    # ─────────────────────────────────────────────────────────────────────

    def create_orchestrator(
        self,
        session_id: str | None = None,
        status: str = "idle",
        system_prompt: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a new orchestrator_agents row.

        Args:
            session_id: Claude SDK session_id for resumption (optional)
            status: idle | executing | waiting | blocked | complete
            system_prompt: Full system prompt at creation
            metadata: JSON dict with model, tools, capabilities

        Returns:
            Dict with id, session_id, status, created_at, updated_at, etc.
        """
        orch_id = f"oa-{uuid.uuid4()}"
        now = int(time.time())
        meta_json = json.dumps(metadata or {})

        with self.conn() as c:
            c.execute(
                """INSERT INTO orchestrator_agents
                   (id, session_id, status, system_prompt, input_tokens, output_tokens,
                    total_cost, metadata, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    orch_id,
                    session_id,
                    status,
                    system_prompt,
                    0,
                    0,
                    0.0,
                    meta_json,
                    now,
                    now,
                ),
            )

        return {
            "id": orch_id,
            "session_id": session_id,
            "status": status,
            "system_prompt": system_prompt,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_cost": 0.0,
            "metadata": metadata or {},
            "created_at": now,
            "updated_at": now,
        }

    def get_orchestrator(self, orchestrator_id: str) -> dict[str, Any] | None:
        """Fetch orchestrator_agents row by id.

        Args:
            orchestrator_id: oa-<uuid>

        Returns:
            Dict or None if not found
        """
        with self.conn() as c:
            row = c.execute(
                "SELECT * FROM orchestrator_agents WHERE id = ?",
                (orchestrator_id,),
            ).fetchone()

        if row is None:
            return None

        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "status": row["status"],
            "system_prompt": row["system_prompt"],
            "working_dir": row["working_dir"],
            "input_tokens": row["input_tokens"],
            "output_tokens": row["output_tokens"],
            "total_cost": row["total_cost"],
            "archived": row["archived"],
            "metadata": json.loads(row["metadata"] or "{}"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def update_orchestrator_session(
        self,
        orchestrator_id: str,
        session_id: str | None,
        status: str,
    ) -> None:
        """Update session_id and status on orchestrator_agents row.

        Args:
            orchestrator_id: oa-<uuid>
            session_id: New Claude SDK session_id (can be None to reset)
            status: New status value
        """
        now = int(time.time())
        with self.conn() as c:
            c.execute(
                """UPDATE orchestrator_agents
                   SET session_id = ?, status = ?, updated_at = ?
                   WHERE id = ?""",
                (session_id, status, now, orchestrator_id),
            )

    def update_orchestrator_costs(
        self,
        orchestrator_id: str,
        input_tokens: int,
        output_tokens: int,
        total_cost: float,
    ) -> None:
        """Update token and cost counters on orchestrator_agents row.

        Args:
            orchestrator_id: oa-<uuid>
            input_tokens: Tokens consumed for input
            output_tokens: Tokens generated
            total_cost: Accumulated USD cost
        """
        now = int(time.time())
        with self.conn() as c:
            c.execute(
                """UPDATE orchestrator_agents
                   SET input_tokens = ?, output_tokens = ?, total_cost = ?,
                       updated_at = ?
                   WHERE id = ?""",
                (input_tokens, output_tokens, total_cost, now, orchestrator_id),
            )

    # ─────────────────────────────────────────────────────────────────────
    # chat_messages CRUD
    # ─────────────────────────────────────────────────────────────────────

    def insert_chat_message(
        self,
        orchestrator_agent_id: str,
        sender_type: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Insert a chat_messages row.

        Args:
            orchestrator_agent_id: oa-<uuid>
            sender_type: user | orchestrator | agent
            message: Full message text
            metadata: JSON dict with cost, tokens, model, type, etc.

        Returns:
            Dict with id, orchestrator_agent_id, sender_type, message, created_at, etc.
        """
        msg_id = f"cm-{uuid.uuid4()}"
        now = int(time.time())
        meta_json = json.dumps(metadata or {})

        with self.conn() as c:
            c.execute(
                """INSERT INTO chat_messages
                   (id, orchestrator_agent_id, sender_type, receiver_type, message,
                    metadata, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    msg_id,
                    orchestrator_agent_id,
                    sender_type,
                    "orchestrator",  # receiver_type (conversations are always to/from orchestrator)
                    message,
                    meta_json,
                    now,
                    now,
                ),
            )

        return {
            "id": msg_id,
            "orchestrator_agent_id": orchestrator_agent_id,
            "sender_type": sender_type,
            "receiver_type": "orchestrator",
            "message": message,
            "metadata": metadata or {},
            "created_at": now,
            "updated_at": now,
        }

    def get_chat_history(
        self,
        orchestrator_agent_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Fetch chat_messages for an orchestrator session, ordered by created_at DESC.

        Args:
            orchestrator_agent_id: oa-<uuid>
            limit: Max number of messages to return (default 50)

        Returns:
            List of dicts, ordered by created_at DESC
        """
        with self.conn() as c:
            rows = c.execute(
                """SELECT * FROM chat_messages
                   WHERE orchestrator_agent_id = ?
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (orchestrator_agent_id, limit),
            ).fetchall()

        messages = []
        for row in reversed(rows):  # Reverse to get chronological order
            messages.append(
                {
                    "id": row["id"],
                    "orchestrator_agent_id": row["orchestrator_agent_id"],
                    "sender_type": row["sender_type"],
                    "receiver_type": row["receiver_type"],
                    "message": row["message"],
                    "summary": row["summary"],
                    "metadata": json.loads(row["metadata"] or "{}"),
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )

        return messages

    # ─────────────────────────────────────────────────────────────────────
    # system_logs CRUD
    # ─────────────────────────────────────────────────────────────────────

    def insert_system_log(
        self,
        level: str,
        message: str,
        metadata: dict[str, Any] | None = None,
        orchestrator_agent_id: str | None = None,
        log_type: str = "app",
    ) -> dict[str, Any]:
        """Insert a system_logs row.

        Args:
            level: DEBUG | INFO | WARNING | ERROR
            message: Human-readable message (stored in content column)
            metadata: JSON dict with type, payload (thinking/tool content)
            orchestrator_agent_id: Optional FK to orchestrator_agents (nullable for app-level logs)
            log_type: thinking | tool_use | hook | response | app

        Returns:
            Dict with id, level, content, payload, timestamp, etc.
        """
        log_id = f"sl-{uuid.uuid4()}"
        now = int(time.time())
        payload_json = json.dumps(metadata or {})

        with self.conn() as c:
            c.execute(
                """INSERT INTO system_logs
                   (id, orchestrator_agent_id, level, log_type, content, payload, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    log_id,
                    orchestrator_agent_id,
                    level,
                    log_type,
                    message,
                    payload_json,
                    now,
                ),
            )

        return {
            "id": log_id,
            "orchestrator_agent_id": orchestrator_agent_id,
            "level": level,
            "log_type": log_type,
            "content": message,
            "payload": metadata or {},
            "timestamp": now,
        }

    # ─────────────────────────────────────────────────────────────────────
    # Aggregation queries
    # ─────────────────────────────────────────────────────────────────────

    def get_turn_count(self, orchestrator_agent_id: str) -> int:
        """Count the number of turns (messages) in a chat session.

        A turn is defined as a user message followed by orchestrator response(s).
        This query counts user-originated messages as a proxy for turn count.

        Args:
            orchestrator_agent_id: oa-<uuid>

        Returns:
            Integer count of turns
        """
        with self.conn() as c:
            row = c.execute(
                """SELECT COUNT(*) as turn_count FROM chat_messages
                   WHERE orchestrator_agent_id = ? AND sender_type = 'user'""",
                (orchestrator_agent_id,),
            ).fetchone()

        return row["turn_count"] if row else 0
