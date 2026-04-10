"""Canonical repository layer for v2 orchestrator tables.

Thin, stateless repository classes that wrap sqlite3 for the four new T062
tables:  orchestrator_agents, agent_instances, chat_messages, system_logs.

Design rules:
- Every method accepts a sqlite3.Connection as its first argument.
  Callers own the connection lifecycle (open / commit / close).
- No global DB state, no connection pooling, no ORM.
- JSON fields (metadata, payload) are serialised on write (json.dumps)
  and deserialised on read (json.loads).
- All queries use ? parameterised placeholders — no f-string SQL.
- IDs are prefixed UUIDs: oa- / ai- / cm- / sl-

Relationship to orch_db.py (T069):
  orch_db.OrchDBAdapter wraps connection management around these repositories.
  db_repositories is the canonical data layer; orch_db delegates to it or
  mirrors its patterns.  Do not duplicate logic in orch_db — prefer calling
  these classes where possible.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a sqlite3.Row to a plain dict."""
    return dict(row)


def _json_loads_field(value: str | None) -> Any:
    """Safely decode a JSON text field; fall back to {} on None / empty."""
    if not value:
        return {}
    return json.loads(value)


def _now() -> int:
    """Return current Unix epoch seconds."""
    return int(time.time())


# ---------------------------------------------------------------------------
# OrchestratorAgentRepo
# ---------------------------------------------------------------------------

class OrchestratorAgentRepo:
    """Repository for the orchestrator_agents table.

    Columns:
        id, session_id, system_prompt, status, working_dir,
        input_tokens, output_tokens, total_cost, archived,
        metadata (JSON text), created_at, updated_at
    """

    # Valid status values (informational, not enforced here)
    STATUSES = frozenset({"idle", "executing", "waiting", "blocked", "complete"})

    @staticmethod
    def create(
        conn: sqlite3.Connection,
        session_id: str | None = None,
        status: str = "idle",
        system_prompt: str | None = None,
        metadata: dict[str, Any] | None = None,
        working_dir: str | None = None,
    ) -> dict[str, Any]:
        """Insert a new orchestrator_agents row.

        Args:
            conn: Open sqlite3.Connection (caller commits).
            session_id: Claude SDK session value (optional).
            status: Initial status string (default 'idle').
            system_prompt: Full system prompt used at creation.
            metadata: Arbitrary JSON-serialisable dict.
            working_dir: CWD at time of spawn (optional).

        Returns:
            Dict representing the new row (metadata already deserialised).
        """
        agent_id = f"oa-{uuid.uuid4()}"
        now = _now()
        meta_json = json.dumps(metadata or {})

        conn.execute(
            """
            INSERT INTO orchestrator_agents
                (id, session_id, status, system_prompt, working_dir,
                 input_tokens, output_tokens, total_cost,
                 archived, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 0, 0, 0.0, 0, ?, ?, ?)
            """,
            (agent_id, session_id, status, system_prompt, working_dir,
             meta_json, now, now),
        )

        return {
            "id": agent_id,
            "session_id": session_id,
            "status": status,
            "system_prompt": system_prompt,
            "working_dir": working_dir,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_cost": 0.0,
            "archived": 0,
            "metadata": metadata or {},
            "created_at": now,
            "updated_at": now,
        }

    @staticmethod
    def get(
        conn: sqlite3.Connection,
        agent_id: str,
    ) -> dict[str, Any] | None:
        """Fetch an orchestrator_agents row by primary key.

        Args:
            conn: Open sqlite3.Connection.
            agent_id: Row id (oa-<uuid>).

        Returns:
            Dict or None if not found.  metadata is a dict (deserialised).
        """
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM orchestrator_agents WHERE id = ?",
            (agent_id,),
        ).fetchone()

        if row is None:
            return None

        result = _row_to_dict(row)
        result["metadata"] = _json_loads_field(result.get("metadata"))
        return result

    @staticmethod
    def get_active(
        conn: sqlite3.Connection,
    ) -> dict[str, Any] | None:
        """Return the most recent non-archived orchestrator_agents row.

        "Active" means archived = 0.  If multiple exist (shouldn't normally
        happen) the most recently created row wins.

        Args:
            conn: Open sqlite3.Connection.

        Returns:
            Dict or None if no active row exists.
        """
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT * FROM orchestrator_agents
            WHERE archived = 0
            ORDER BY created_at DESC
            LIMIT 1
            """,
        ).fetchone()

        if row is None:
            return None

        result = _row_to_dict(row)
        result["metadata"] = _json_loads_field(result.get("metadata"))
        return result

    @staticmethod
    def update_status(
        conn: sqlite3.Connection,
        agent_id: str,
        status: str,
    ) -> None:
        """Update the status column on an orchestrator_agents row.

        Args:
            conn: Open sqlite3.Connection (caller commits).
            agent_id: Row id (oa-<uuid>).
            status: New status value.
        """
        conn.execute(
            """
            UPDATE orchestrator_agents
               SET status = ?, updated_at = ?
             WHERE id = ?
            """,
            (status, _now(), agent_id),
        )

    @staticmethod
    def update_session(
        conn: sqlite3.Connection,
        agent_id: str,
        session_id: str | None,
    ) -> None:
        """Update the session_id on an orchestrator_agents row.

        Args:
            conn: Open sqlite3.Connection (caller commits).
            agent_id: Row id (oa-<uuid>).
            session_id: New Claude SDK session value (can be None).
        """
        conn.execute(
            """
            UPDATE orchestrator_agents
               SET session_id = ?, updated_at = ?
             WHERE id = ?
            """,
            (session_id, _now(), agent_id),
        )

    @staticmethod
    def update_costs(
        conn: sqlite3.Connection,
        agent_id: str,
        input_tokens: int,
        output_tokens: int,
        total_cost: float,
    ) -> None:
        """Overwrite token and cost counters on an orchestrator_agents row.

        These are accumulated totals (not deltas) — callers are responsible
        for adding previous values before calling.

        Args:
            conn: Open sqlite3.Connection (caller commits).
            agent_id: Row id (oa-<uuid>).
            input_tokens: New accumulated input token count.
            output_tokens: New accumulated output token count.
            total_cost: New accumulated USD cost.
        """
        conn.execute(
            """
            UPDATE orchestrator_agents
               SET input_tokens = ?, output_tokens = ?, total_cost = ?,
                   updated_at = ?
             WHERE id = ?
            """,
            (input_tokens, output_tokens, total_cost, _now(), agent_id),
        )

    @staticmethod
    def archive(
        conn: sqlite3.Connection,
        agent_id: str,
    ) -> None:
        """Soft-delete an orchestrator_agents row by setting archived = 1.

        Args:
            conn: Open sqlite3.Connection (caller commits).
            agent_id: Row id (oa-<uuid>).
        """
        conn.execute(
            """
            UPDATE orchestrator_agents
               SET archived = 1, updated_at = ?
             WHERE id = ?
            """,
            (_now(), agent_id),
        )


# ---------------------------------------------------------------------------
# AgentInstanceRepo
# ---------------------------------------------------------------------------

class AgentInstanceRepo:
    """Repository for the agent_instances table.

    Columns:
        id, orchestrator_agent_id, name, model, system_prompt, working_dir,
        git_worktree, status, session_id, adw_id, adw_step,
        input_tokens, output_tokens, total_cost, archived,
        metadata (JSON text), created_at, updated_at
    """

    @staticmethod
    def create(
        conn: sqlite3.Connection,
        orchestrator_agent_id: str,
        adw_id: str | None = None,
        session_id: str | None = None,
        status: str = "idle",
        phase: str | None = None,
        issue_number: int | None = None,
        cleo_task_id: str | None = None,
        name: str = "agent",
        model: str = "claude-sonnet-4-5",
        system_prompt: str | None = None,
        working_dir: str | None = None,
        git_worktree: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Insert a new agent_instances row.

        The task specification provides a compact signature; additional
        columns (name, model, system_prompt, working_dir, git_worktree)
        are accepted as kwargs with sensible defaults so callers can
        supply full detail when available.

        Args:
            conn: Open sqlite3.Connection (caller commits).
            orchestrator_agent_id: FK to orchestrator_agents.id.
            adw_id: Links to runs.adw_id (optional).
            session_id: Claude SDK session for resumption (optional).
            status: Initial status (default 'idle').
            phase: PITER phase string stored in adw_step (optional).
            issue_number: GitHub issue number (stored in metadata if provided).
            cleo_task_id: CLEO task id (stored in metadata if provided).
            name: Human label for this instance (default 'agent').
            model: Model identifier (default 'claude-sonnet-4-5').
            system_prompt: System prompt used at spawn.
            working_dir: CWD at time of spawn.
            git_worktree: Git worktree path if applicable.
            metadata: Arbitrary JSON-serialisable dict.

        Returns:
            Dict representing the new row.
        """
        instance_id = f"ai-{uuid.uuid4()}"
        now = _now()

        # Merge convenience fields into metadata
        extra: dict[str, Any] = {}
        if issue_number is not None:
            extra["issue_number"] = issue_number
        if cleo_task_id is not None:
            extra["cleo_task_id"] = cleo_task_id
        merged_meta = {**(metadata or {}), **extra}
        meta_json = json.dumps(merged_meta)

        conn.execute(
            """
            INSERT INTO agent_instances
                (id, orchestrator_agent_id, name, model,
                 system_prompt, working_dir, git_worktree,
                 status, session_id, adw_id, adw_step,
                 input_tokens, output_tokens, total_cost,
                 archived, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0.0, 0, ?, ?, ?)
            """,
            (
                instance_id, orchestrator_agent_id, name, model,
                system_prompt, working_dir, git_worktree,
                status, session_id, adw_id, phase,
                meta_json, now, now,
            ),
        )

        return {
            "id": instance_id,
            "orchestrator_agent_id": orchestrator_agent_id,
            "name": name,
            "model": model,
            "system_prompt": system_prompt,
            "working_dir": working_dir,
            "git_worktree": git_worktree,
            "status": status,
            "session_id": session_id,
            "adw_id": adw_id,
            "adw_step": phase,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_cost": 0.0,
            "archived": 0,
            "metadata": merged_meta,
            "created_at": now,
            "updated_at": now,
        }

    @staticmethod
    def get(
        conn: sqlite3.Connection,
        instance_id: str,
    ) -> dict[str, Any] | None:
        """Fetch an agent_instances row by primary key.

        Args:
            conn: Open sqlite3.Connection.
            instance_id: Row id (ai-<uuid>).

        Returns:
            Dict or None.  metadata is deserialised.
        """
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM agent_instances WHERE id = ?",
            (instance_id,),
        ).fetchone()

        if row is None:
            return None

        result = _row_to_dict(row)
        result["metadata"] = _json_loads_field(result.get("metadata"))
        return result

    @staticmethod
    def list_for_adw(
        conn: sqlite3.Connection,
        adw_id: str,
    ) -> list[dict[str, Any]]:
        """Return all agent_instances rows for a given ADW run.

        Args:
            conn: Open sqlite3.Connection.
            adw_id: ADW identifier (links to runs.adw_id).

        Returns:
            List of dicts ordered by created_at ASC.
        """
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT * FROM agent_instances
             WHERE adw_id = ?
             ORDER BY created_at ASC
            """,
            (adw_id,),
        ).fetchall()

        result = []
        for row in rows:
            d = _row_to_dict(row)
            d["metadata"] = _json_loads_field(d.get("metadata"))
            result.append(d)
        return result

    @staticmethod
    def list_for_orchestrator(
        conn: sqlite3.Connection,
        orchestrator_agent_id: str,
    ) -> list[dict[str, Any]]:
        """Return all agent_instances rows for a given orchestrator session.

        Args:
            conn: Open sqlite3.Connection.
            orchestrator_agent_id: FK to orchestrator_agents.id.

        Returns:
            List of dicts ordered by created_at ASC.
        """
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT * FROM agent_instances
             WHERE orchestrator_agent_id = ?
             ORDER BY created_at ASC
            """,
            (orchestrator_agent_id,),
        ).fetchall()

        result = []
        for row in rows:
            d = _row_to_dict(row)
            d["metadata"] = _json_loads_field(d.get("metadata"))
            result.append(d)
        return result

    @staticmethod
    def update_status(
        conn: sqlite3.Connection,
        instance_id: str,
        status: str,
        phase: str | None = None,
    ) -> None:
        """Update status (and optionally adw_step/phase) on an agent_instances row.

        Args:
            conn: Open sqlite3.Connection (caller commits).
            instance_id: Row id (ai-<uuid>).
            status: New status value.
            phase: New PITER phase string for adw_step (optional; unchanged if None).
        """
        if phase is not None:
            conn.execute(
                """
                UPDATE agent_instances
                   SET status = ?, adw_step = ?, updated_at = ?
                 WHERE id = ?
                """,
                (status, phase, _now(), instance_id),
            )
        else:
            conn.execute(
                """
                UPDATE agent_instances
                   SET status = ?, updated_at = ?
                 WHERE id = ?
                """,
                (status, _now(), instance_id),
            )

    @staticmethod
    def update_costs(
        conn: sqlite3.Connection,
        instance_id: str,
        input_tokens: int,
        output_tokens: int,
        total_cost: float,
    ) -> None:
        """Overwrite token and cost counters on an agent_instances row.

        Args:
            conn: Open sqlite3.Connection (caller commits).
            instance_id: Row id (ai-<uuid>).
            input_tokens: New accumulated input token count.
            output_tokens: New accumulated output token count.
            total_cost: New accumulated USD cost.
        """
        conn.execute(
            """
            UPDATE agent_instances
               SET input_tokens = ?, output_tokens = ?, total_cost = ?,
                   updated_at = ?
             WHERE id = ?
            """,
            (input_tokens, output_tokens, total_cost, _now(), instance_id),
        )

    @staticmethod
    def complete(
        conn: sqlite3.Connection,
        instance_id: str,
        status: str = "complete",
    ) -> None:
        """Mark an agent_instances row as complete.

        Sets status to the supplied value (default 'complete') and
        soft-archives the row (archived = 1).

        Args:
            conn: Open sqlite3.Connection (caller commits).
            instance_id: Row id (ai-<uuid>).
            status: Final status (default 'complete').
        """
        conn.execute(
            """
            UPDATE agent_instances
               SET status = ?, archived = 1, updated_at = ?
             WHERE id = ?
            """,
            (status, _now(), instance_id),
        )


# ---------------------------------------------------------------------------
# ChatMessageRepo
# ---------------------------------------------------------------------------

class ChatMessageRepo:
    """Repository for the chat_messages table.

    Columns:
        id, orchestrator_agent_id, sender_type, receiver_type,
        message, summary, agent_id, session_id,
        metadata (JSON text), created_at, updated_at
    """

    @staticmethod
    def insert(
        conn: sqlite3.Connection,
        orchestrator_agent_id: str,
        sender_type: str,
        message: str,
        metadata: dict[str, Any] | None = None,
        receiver_type: str = "orchestrator",
        agent_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Insert a new chat_messages row.

        Args:
            conn: Open sqlite3.Connection (caller commits).
            orchestrator_agent_id: FK to orchestrator_agents.id.
            sender_type: 'user' | 'orchestrator' | 'agent'.
            message: Full text content.
            metadata: JSON dict (cost, tokens, model, etc.).
            receiver_type: 'user' | 'orchestrator' | 'agent' (default 'orchestrator').
            agent_id: Optional FK to agent_instances.id.
            session_id: Claude SDK session ID at time of message.

        Returns:
            Dict representing the inserted row.
        """
        msg_id = f"cm-{uuid.uuid4()}"
        now = _now()
        meta_json = json.dumps(metadata or {})

        conn.execute(
            """
            INSERT INTO chat_messages
                (id, orchestrator_agent_id, sender_type, receiver_type,
                 message, summary, agent_id, session_id,
                 metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)
            """,
            (
                msg_id, orchestrator_agent_id, sender_type, receiver_type,
                message, agent_id, session_id,
                meta_json, now, now,
            ),
        )

        return {
            "id": msg_id,
            "orchestrator_agent_id": orchestrator_agent_id,
            "sender_type": sender_type,
            "receiver_type": receiver_type,
            "message": message,
            "summary": None,
            "agent_id": agent_id,
            "session_id": session_id,
            "metadata": metadata or {},
            "created_at": now,
            "updated_at": now,
        }

    @staticmethod
    def get_history(
        conn: sqlite3.Connection,
        orchestrator_agent_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return chat history for an orchestrator session in chronological order.

        Rows are ordered by created_at ASC so callers can replay the
        conversation for session reconstruction.

        Args:
            conn: Open sqlite3.Connection.
            orchestrator_agent_id: FK to orchestrator_agents.id.
            limit: Maximum number of rows (default 50).

        Returns:
            List of dicts ordered by created_at ASC.
        """
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT * FROM chat_messages
             WHERE orchestrator_agent_id = ?
             ORDER BY created_at ASC
             LIMIT ?
            """,
            (orchestrator_agent_id, limit),
        ).fetchall()

        result = []
        for row in rows:
            d = _row_to_dict(row)
            d["metadata"] = _json_loads_field(d.get("metadata"))
            result.append(d)
        return result

    @staticmethod
    def update_summary(
        conn: sqlite3.Connection,
        message_id: str,
        summary: str,
    ) -> None:
        """Populate the summary column on a chat_messages row.

        Called asynchronously by the AI summariser after the message is saved.

        Args:
            conn: Open sqlite3.Connection (caller commits).
            message_id: Row id (cm-<uuid>).
            summary: AI-generated summary text.
        """
        conn.execute(
            """
            UPDATE chat_messages
               SET summary = ?, updated_at = ?
             WHERE id = ?
            """,
            (summary, _now(), message_id),
        )


# ---------------------------------------------------------------------------
# SystemLogRepo
# ---------------------------------------------------------------------------

class SystemLogRepo:
    """Repository for the system_logs table.

    Columns:
        id, orchestrator_agent_id, agent_id, session_id, adw_id, adw_step,
        level, log_type, event_type, content, payload (JSON text),
        summary, entry_index, timestamp
    """

    @staticmethod
    def insert(
        conn: sqlite3.Connection,
        level: str,
        message: str,
        metadata: dict[str, Any] | None = None,
        orchestrator_agent_id: str | None = None,
        agent_id: str | None = None,
        session_id: str | None = None,
        adw_id: str | None = None,
        adw_step: str | None = None,
        log_type: str = "app",
        event_type: str | None = None,
        entry_index: int | None = None,
    ) -> dict[str, Any]:
        """Insert a new system_logs row.

        App-level logs (log_type='app') have orchestrator_agent_id=None
        and agent_id=None — they are global system events.

        Args:
            conn: Open sqlite3.Connection (caller commits).
            level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR'.
            message: Human-readable text (stored in content column).
            metadata: Full structured data (stored as JSON in payload).
            orchestrator_agent_id: Optional FK to orchestrator_agents.id.
            agent_id: Optional FK to agent_instances.id.
            session_id: Claude SDK session at time of log (optional).
            adw_id: Links to runs.adw_id (optional).
            adw_step: PITER phase string (optional).
            log_type: 'thinking' | 'tool_use' | 'hook' | 'response' | 'app'.
            event_type: Specific event name (PreToolUse, Stop, etc.).
            entry_index: Position within a conversation turn.

        Returns:
            Dict representing the inserted row.
        """
        log_id = f"sl-{uuid.uuid4()}"
        now = _now()
        payload_json = json.dumps(metadata or {})

        conn.execute(
            """
            INSERT INTO system_logs
                (id, orchestrator_agent_id, agent_id, session_id,
                 adw_id, adw_step, level, log_type, event_type,
                 content, payload, summary, entry_index, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (
                log_id, orchestrator_agent_id, agent_id, session_id,
                adw_id, adw_step, level, log_type, event_type,
                message, payload_json, entry_index, now,
            ),
        )

        return {
            "id": log_id,
            "orchestrator_agent_id": orchestrator_agent_id,
            "agent_id": agent_id,
            "session_id": session_id,
            "adw_id": adw_id,
            "adw_step": adw_step,
            "level": level,
            "log_type": log_type,
            "event_type": event_type,
            "content": message,
            "payload": metadata or {},
            "summary": None,
            "entry_index": entry_index,
            "timestamp": now,
        }

    @staticmethod
    def get_for_session(
        conn: sqlite3.Connection,
        session_id: str,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Return system_logs for a given Claude SDK session_id.

        Args:
            conn: Open sqlite3.Connection.
            session_id: Claude SDK session value.
            limit: Maximum number of rows (default 500).

        Returns:
            List of dicts ordered by timestamp ASC.
        """
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT * FROM system_logs
             WHERE session_id = ?
             ORDER BY timestamp ASC
             LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()

        result = []
        for row in rows:
            d = _row_to_dict(row)
            d["payload"] = _json_loads_field(d.get("payload"))
            result.append(d)
        return result

    @staticmethod
    def get_for_adw(
        conn: sqlite3.Connection,
        adw_id: str,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Return system_logs for a given ADW run.

        Args:
            conn: Open sqlite3.Connection.
            adw_id: ADW identifier (links to runs.adw_id).
            limit: Maximum number of rows (default 500).

        Returns:
            List of dicts ordered by timestamp ASC.
        """
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT * FROM system_logs
             WHERE adw_id = ?
             ORDER BY timestamp ASC
             LIMIT ?
            """,
            (adw_id, limit),
        ).fetchall()

        result = []
        for row in rows:
            d = _row_to_dict(row)
            d["payload"] = _json_loads_field(d.get("payload"))
            result.append(d)
        return result

    @staticmethod
    def update_summary(
        conn: sqlite3.Connection,
        log_id: str,
        summary: str,
    ) -> None:
        """Populate the summary column on a system_logs row.

        Called asynchronously by the AI summariser.

        Args:
            conn: Open sqlite3.Connection (caller commits).
            log_id: Row id (sl-<uuid>).
            summary: AI-generated summary text.
        """
        conn.execute(
            """
            UPDATE system_logs
               SET summary = ?
             WHERE id = ?
            """,
            (summary, log_id),
        )
