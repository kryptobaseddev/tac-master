"""OrchestratorAgentModel — high-level session lifecycle for the persistent orchestrator.

This module wraps the thin repository layer in db_repositories.py with a
stateful class that owns the sqlite3 connection lifecycle and exposes the
four operations consumed by OrchestratorService (T057):

    spawn_session   — create orchestrator_agents row, return id
    resume_session  — load + validate an existing session
    record_response — insert chat_messages + update costs (atomic)
    log_block       — insert system_logs with serialised payload
    close_session   — archive the orchestrator_agents row
    get_active      — return the current active session or None

Design rules (mirror db_repositories.py):
- OrchestratorAgentModel owns exactly one sqlite3.Connection.
- WAL + foreign_keys pragmas applied on __init__.
- All writes that span multiple tables are wrapped in explicit transactions
  to guarantee atomicity (record_response: insert + update).
- No global DB state, no ORM, no connection pooling.
- JSON serialisation delegated to the repos (db_repositories already handles it).
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .db_repositories import (
    ChatMessageRepo,
    OrchestratorAgentRepo,
    SystemLogRepo,
)


class OrchestratorAgentModel:
    """Stateful wrapper around the orchestrator_agents session lifecycle.

    One instance per process — holds a single sqlite3.Connection.  Call
    close() (or use as a context manager) when done to release the handle.

    Args:
        db_path: Absolute path to the SQLite database file.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection = sqlite3.connect(db_path, check_same_thread=False)
        # Enable WAL for concurrent readers + single-writer safety.
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.commit()

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def __enter__(self) -> "OrchestratorAgentModel":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying database connection."""
        try:
            self._conn.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def spawn_session(
        self,
        system_prompt: str,
        working_dir: str | None = None,
    ) -> str:
        """Create a new orchestrator_agents row and return its id.

        Args:
            system_prompt: Full system prompt for this orchestrator session.
            working_dir: CWD at time of spawn (optional).

        Returns:
            The new agent id in the format ``oa-<uuid4>``.
        """
        row = OrchestratorAgentRepo.create(
            self._conn,
            system_prompt=system_prompt,
            working_dir=working_dir,
            status="idle",
        )
        self._conn.commit()
        return row["id"]

    def resume_session(self, agent_id: str) -> dict[str, Any]:
        """Load an existing orchestrator session and verify it is resumable.

        Args:
            agent_id: The ``oa-<uuid4>`` id of the session to resume.

        Returns:
            Dict representing the orchestrator_agents row (metadata deserialised).

        Raises:
            ValueError: If the session does not exist or has been archived.
        """
        row = OrchestratorAgentRepo.get(self._conn, agent_id)

        if row is None:
            raise ValueError(f"OrchestratorAgent not found: {agent_id!r}")

        if row.get("archived"):
            raise ValueError(
                f"OrchestratorAgent {agent_id!r} is archived and cannot be resumed"
            )

        return row

    def record_response(
        self,
        agent_id: str,
        message: str,
        sender_type: str,
        cost: float = 0.0,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> dict[str, Any]:
        """Insert a chat message and atomically update token/cost accumulators.

        Both the ``chat_messages`` insert and the ``orchestrator_agents``
        cost update are executed within a single transaction so that a
        crash between the two writes cannot produce inconsistent state.

        Args:
            agent_id: The ``oa-<uuid4>`` id of the orchestrator session.
            message: Full text content of the message.
            sender_type: ``'user'`` | ``'orchestrator'`` | ``'agent'``.
            cost: Incremental USD cost for this message (default 0.0).
            input_tokens: Incremental input token count (default 0).
            output_tokens: Incremental output token count (default 0).

        Returns:
            Dict representing the inserted chat_messages row.
        """
        # Build metadata to attach cost/token info to the message row.
        msg_metadata: dict[str, Any] = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost,
        }

        with self._conn:
            # Insert chat message.
            chat_row = ChatMessageRepo.insert(
                self._conn,
                orchestrator_agent_id=agent_id,
                sender_type=sender_type,
                message=message,
                metadata=msg_metadata,
            )

            # Read current totals so the update can accumulate correctly.
            current = OrchestratorAgentRepo.get(self._conn, agent_id)
            if current is None:
                raise ValueError(
                    f"OrchestratorAgent not found during record_response: {agent_id!r}"
                )

            new_input = current.get("input_tokens", 0) + input_tokens
            new_output = current.get("output_tokens", 0) + output_tokens
            new_cost = (current.get("total_cost") or 0.0) + cost

            OrchestratorAgentRepo.update_costs(
                self._conn,
                agent_id=agent_id,
                input_tokens=new_input,
                output_tokens=new_output,
                total_cost=new_cost,
            )
            # ``with self._conn`` commits automatically on block exit.

        return chat_row

    def log_block(
        self,
        agent_id: str,
        log_type: str,
        content: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Insert a system_logs row for a thinking/tool-use/hook/response/app event.

        Args:
            agent_id: The ``oa-<uuid4>`` id of the orchestrator session.
            log_type: ``'thinking'`` | ``'tool_use'`` | ``'hook'`` | ``'response'`` | ``'app'``.
            content: Human-readable text (stored in the ``content`` column).
            payload: Arbitrary structured data serialised to JSON (stored in
                ``payload``).  Defaults to ``{}`` when ``None``.

        Returns:
            Dict representing the inserted system_logs row.
        """
        # log_type doubles as the level for callers that do not specify one
        # separately.  Map known log_type values to conventional log levels;
        # fall back to 'INFO' for everything else.
        _type_to_level: dict[str, str] = {
            "thinking": "DEBUG",
            "tool_use": "INFO",
            "hook": "INFO",
            "response": "INFO",
            "app": "INFO",
        }
        level = _type_to_level.get(log_type, "INFO")

        row = SystemLogRepo.insert(
            self._conn,
            level=level,
            message=content,
            metadata=payload,
            orchestrator_agent_id=agent_id,
            log_type=log_type,
        )
        self._conn.commit()
        return row

    def close_session(self, agent_id: str) -> None:
        """Archive an orchestrator session (soft-delete).

        Sets ``archived = 1`` on the orchestrator_agents row via
        ``OrchestratorAgentRepo.archive()``.

        Args:
            agent_id: The ``oa-<uuid4>`` id of the session to close.
        """
        OrchestratorAgentRepo.archive(self._conn, agent_id)
        self._conn.commit()

    def get_active(self) -> dict[str, Any] | None:
        """Return the current active orchestrator session or None.

        "Active" means ``archived = 0``.  If multiple rows exist (edge case
        after a crash), the most recently created row is returned.

        Returns:
            Dict representing the row or ``None`` if no active session exists.
        """
        return OrchestratorAgentRepo.get_active(self._conn)
