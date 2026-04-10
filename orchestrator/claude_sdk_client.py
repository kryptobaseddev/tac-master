"""ClaudeSDKClient — wraps the `claude` CLI with persistent session management.

Provides:
- create_session()      Start a fresh Claude session, returning a new session_id.
- resume_session()      Verify an existing session_id is present in the DB.
- send_message()        Stream a response from `claude --print --output-format stream-json`
                        as typed events (TextBlock, ThinkingBlock, ToolUseBlock).
- Token usage and cost  Extracted from the ResultMessage at end of stream.
- Session persistence   session_id stored in orchestrator_agents via OrchestratorAgentRepo.

Usage example::

    client = ClaudeSDKClient(
        state_store=store,
        orchestrator_id="oa-<uuid>",
        cwd="/srv/tac-master",
    )

    # First message — fresh session
    async for event in client.send_message("What repos are queued?"):
        if isinstance(event, TextBlock):
            print(event.text)
        elif isinstance(event, ResultMessage):
            print(f"Cost: {event.cost_usd}")
            print(f"Session: {event.session_id}")

    # Second message — resumes the same session automatically
    async for event in client.send_message("Show me more details"):
        ...

Design notes
------------
- The `claude` CLI JSONL stream emits one JSON object per line.
- system message  (type=system, subtype=init)     → carries session_id
- assistant msgs  (type=assistant)                → content blocks array
- result message  (type=result, subtype=success)  → final usage + session_id
- We spawn `claude` as an async subprocess feeding the message via stdin
  (when --input-format text is combined with --print).
- The `--resume <session_id>` flag is used for all subsequent invocations.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from asyncio.subprocess import PIPE
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Typed event dataclasses (mirrors Claude SDK content block types)
# ---------------------------------------------------------------------------


@dataclass
class TextBlock:
    """A text chunk from an assistant message."""

    text: str


@dataclass
class ThinkingBlock:
    """An extended thinking block from an assistant message."""

    thinking: str


@dataclass
class ToolUseBlock:
    """A tool-use block from an assistant message."""

    id: str
    name: str
    input: dict


@dataclass
class ResultMessage:
    """The final result message that ends a streaming response.

    Emitted once per `send_message()` call, always last in the iterator.
    """

    subtype: str            # "success" | "error" | "interrupted"
    session_id: Optional[str] = None
    result: Optional[str] = None
    error: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cost_usd: Optional[float] = None

    @property
    def is_error(self) -> bool:
        return self.subtype == "error"

    @property
    def is_success(self) -> bool:
        return self.subtype == "success"


# Union type for all stream events
StreamEvent = TextBlock | ThinkingBlock | ToolUseBlock | ResultMessage


# ---------------------------------------------------------------------------
# OrchestratorAgentRepo — minimal DB accessor for session persistence
# ---------------------------------------------------------------------------


class OrchestratorAgentRepo:
    """Thin SQLite accessor for the orchestrator_agents table.

    Wraps StateStore to read/write session_id and token accumulators.
    The SCHEMA_V2 in state_store.py already defines this table — this
    class is a focused access layer, not a full ORM.
    """

    def __init__(self, state_store) -> None:
        self._store = state_store

    def get_session_id(self, orchestrator_id: str) -> Optional[str]:
        """Return the persisted session_id for an orchestrator row, or None."""
        with self._store.conn() as c:
            row = c.execute(
                "SELECT session_id FROM orchestrator_agents WHERE id = ?",
                (orchestrator_id,),
            ).fetchone()
            if row is None:
                return None
            return row["session_id"]

    def upsert_orchestrator(
        self,
        orchestrator_id: str,
        *,
        session_id: Optional[str] = None,
        status: str = "idle",
        working_dir: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> None:
        """Insert or update an orchestrator_agents row.

        Uses INSERT OR IGNORE + UPDATE to avoid clobbering existing rows when
        called multiple times with a fresh orchestrator_id.
        """
        now = int(time.time())
        with self._store.conn() as c:
            c.execute(
                """
                INSERT OR IGNORE INTO orchestrator_agents
                    (id, session_id, status, working_dir, system_prompt,
                     input_tokens, output_tokens, total_cost,
                     archived, metadata, created_at, updated_at)
                VALUES
                    (?, ?, ?, ?, ?,
                     0, 0, 0.0,
                     0, '{}', ?, ?)
                """,
                (
                    orchestrator_id,
                    session_id,
                    status,
                    working_dir,
                    system_prompt,
                    now,
                    now,
                ),
            )
            c.execute(
                """
                UPDATE orchestrator_agents
                SET session_id  = COALESCE(?, session_id),
                    status      = ?,
                    working_dir = COALESCE(?, working_dir),
                    updated_at  = ?
                WHERE id = ?
                """,
                (session_id, status, working_dir, now, orchestrator_id),
            )

    def update_session_id(self, orchestrator_id: str, session_id: str) -> None:
        """Persist a new session_id after a successful first-turn response."""
        now = int(time.time())
        with self._store.conn() as c:
            c.execute(
                "UPDATE orchestrator_agents SET session_id = ?, updated_at = ? WHERE id = ?",
                (session_id, now, orchestrator_id),
            )

    def accumulate_tokens(
        self,
        orchestrator_id: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        """Add token/cost totals to the running accumulator."""
        now = int(time.time())
        with self._store.conn() as c:
            c.execute(
                """
                UPDATE orchestrator_agents
                SET input_tokens  = input_tokens  + ?,
                    output_tokens = output_tokens + ?,
                    total_cost    = total_cost    + ?,
                    updated_at    = ?
                WHERE id = ?
                """,
                (input_tokens, output_tokens, cost_usd, now, orchestrator_id),
            )

    def set_status(self, orchestrator_id: str, status: str) -> None:
        """Update status field (idle | executing | waiting | blocked | complete)."""
        now = int(time.time())
        with self._store.conn() as c:
            c.execute(
                "UPDATE orchestrator_agents SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, orchestrator_id),
            )


# ---------------------------------------------------------------------------
# ClaudeSDKClient
# ---------------------------------------------------------------------------


_CLAUDE_CLI_DEFAULT = os.environ.get("CLAUDE_CLI", "claude")

# JSONL output fields we extract from assistant content blocks
_BLOCK_TYPES = {
    "text": TextBlock,
    "thinking": ThinkingBlock,
    "tool_use": ToolUseBlock,
}


def _parse_content_blocks(content: list[dict]) -> list[TextBlock | ThinkingBlock | ToolUseBlock]:
    """Parse the `content` array from an assistant JSONL message into typed blocks."""
    blocks: list[TextBlock | ThinkingBlock | ToolUseBlock] = []
    for item in content:
        btype = item.get("type", "")
        if btype == "text":
            blocks.append(TextBlock(text=item.get("text", "")))
        elif btype == "thinking":
            blocks.append(ThinkingBlock(thinking=item.get("thinking", "")))
        elif btype == "tool_use":
            blocks.append(
                ToolUseBlock(
                    id=item.get("id", ""),
                    name=item.get("name", ""),
                    input=item.get("input", {}),
                )
            )
    return blocks


class ClaudeSDKClient:
    """Persistent session wrapper around the `claude` CLI.

    Maintains a single ``session_id`` per instance so that consecutive calls
    to :meth:`send_message` continue the same conversation. The session_id is
    persisted to SQLite via :class:`OrchestratorAgentRepo` so that the service
    can survive restarts.

    Parameters
    ----------
    state_store:
        StateStore instance (with V2 schema already applied).
    orchestrator_id:
        The primary-key of the ``orchestrator_agents`` row for this session.
        The row must already exist (create it via :meth:`create_session`).
    cwd:
        Working directory for the `claude` subprocess.
    system_prompt:
        Optional system prompt override passed via ``--system-prompt``.
    model:
        Claude model alias or full name (e.g. ``"sonnet"`` or ``"claude-sonnet-4-6"``).
    claude_cli:
        Path / command name for the `claude` binary. Defaults to ``"claude"``
        (reads ``CLAUDE_CLI`` env var first).
    """

    def __init__(
        self,
        state_store,
        orchestrator_id: str,
        cwd: Optional[str] = None,
        system_prompt: Optional[str] = None,
        model: str = "sonnet",
        claude_cli: str = _CLAUDE_CLI_DEFAULT,
    ) -> None:
        self._repo = OrchestratorAgentRepo(state_store)
        self._orchestrator_id = orchestrator_id
        self._cwd = cwd
        self._system_prompt = system_prompt
        self._model = model
        self._cli = claude_cli
        # In-memory session_id cache (loaded from DB on first use)
        self._session_id: Optional[str] = None
        self._session_loaded = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_session(
        self,
        system_prompt: Optional[str] = None,
        cwd: Optional[str] = None,
    ) -> str:
        """Register a new orchestrator session in the database.

        This does **not** spawn a claude subprocess. The actual Claude session
        is created on the first :meth:`send_message` call. The returned ID is
        the ``orchestrator_id`` (a stable application-level UUID), not the
        Claude session_id (which is unknown until the first response).

        Returns the ``orchestrator_id`` so callers can reference this client.
        """
        if system_prompt:
            self._system_prompt = system_prompt
        if cwd:
            self._cwd = cwd

        self._repo.upsert_orchestrator(
            self._orchestrator_id,
            session_id=None,
            status="idle",
            working_dir=self._cwd,
            system_prompt=self._system_prompt,
        )
        self._session_id = None
        self._session_loaded = True
        log.info("create_session: registered orchestrator_id=%s", self._orchestrator_id)
        return self._orchestrator_id

    def resume_session(self, session_id: str, cwd: Optional[str] = None) -> bool:
        """Load an existing session from the database.

        Looks up the ``orchestrator_agents`` row by ``orchestrator_id``.  If
        the stored ``session_id`` matches (or if you're passing the known
        session_id explicitly to hydrate this client), sets the in-memory
        ``_session_id`` so subsequent :meth:`send_message` calls will pass
        ``--resume <session_id>`` to the CLI.

        Parameters
        ----------
        session_id:
            The Claude SDK session_id to resume (passed via ``--resume``).
        cwd:
            Override the working directory for this session.

        Returns
        -------
        bool
            ``True`` if the session_id was accepted and stored in-memory,
            ``False`` if the orchestrator_id row does not exist.
        """
        if cwd:
            self._cwd = cwd

        stored = self._repo.get_session_id(self._orchestrator_id)
        if stored is None:
            # Row might not exist yet — try to upsert it
            self._repo.upsert_orchestrator(
                self._orchestrator_id,
                session_id=session_id,
                status="idle",
                working_dir=self._cwd,
            )
            self._session_id = session_id
            self._session_loaded = True
            log.info(
                "resume_session: created new row with orchestrator_id=%s session_id=%s",
                self._orchestrator_id,
                session_id,
            )
            return True

        # Accept the passed session_id and store it (caller is authoritative)
        self._session_id = session_id
        self._session_loaded = True
        if stored != session_id:
            self._repo.update_session_id(self._orchestrator_id, session_id)
            log.info(
                "resume_session: updated session_id for orchestrator_id=%s",
                self._orchestrator_id,
            )
        else:
            log.info(
                "resume_session: loaded session_id=%s for orchestrator_id=%s",
                session_id,
                self._orchestrator_id,
            )
        return True

    async def send_message(
        self,
        message: str,
        *,
        max_turns: Optional[int] = None,
        extra_args: Optional[list[str]] = None,
    ) -> AsyncIterator[StreamEvent]:
        """Send *message* to Claude and yield typed stream events.

        Internally:
        1. Builds the ``claude --print --output-format stream-json ...`` command.
        2. Spawns it with ``asyncio.create_subprocess_exec``, feeding *message*
           via stdin.
        3. Reads stdout line-by-line, parsing each JSONL object into typed
           events and yielding them.
        4. On the ``ResultMessage``, persists the new ``session_id`` and
           accumulates token/cost counters in SQLite.

        Yields
        ------
        TextBlock | ThinkingBlock | ToolUseBlock | ResultMessage
            In stream order. ``ResultMessage`` is always last.
        """
        # Lazy-load session_id from DB if not yet loaded
        if not self._session_loaded:
            self._session_id = self._repo.get_session_id(self._orchestrator_id)
            self._session_loaded = True

        cmd = self._build_command(max_turns=max_turns, extra_args=extra_args)

        log.debug(
            "send_message: spawning %s with session_id=%s cwd=%s",
            " ".join(cmd[:4]),
            self._session_id,
            self._cwd,
        )

        # Mark orchestrator as executing
        self._repo.set_status(self._orchestrator_id, "executing")

        try:
            async for event in self._stream(cmd, message):
                yield event
        finally:
            self._repo.set_status(self._orchestrator_id, "idle")

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def session_id(self) -> Optional[str]:
        """The current Claude SDK session_id (None if no message sent yet)."""
        return self._session_id

    @property
    def orchestrator_id(self) -> str:
        """The application-level orchestrator_agents primary key."""
        return self._orchestrator_id

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_command(
        self,
        *,
        max_turns: Optional[int],
        extra_args: Optional[list[str]],
    ) -> list[str]:
        """Construct the ``claude`` CLI command list."""
        cmd = [
            self._cli,
            "--print",
            "--output-format", "stream-json",
            "--input-format", "text",
            "--dangerously-skip-permissions",
            "--model", self._model,
        ]

        if self._session_id:
            cmd += ["--resume", self._session_id]

        if self._system_prompt:
            cmd += ["--system-prompt", self._system_prompt]

        if max_turns is not None:
            cmd += ["--max-turns", str(max_turns)]

        if self._cwd:
            # We set cwd on the subprocess — no CLI flag needed.
            pass

        if extra_args:
            cmd.extend(extra_args)

        return cmd

    async def _stream(
        self, cmd: list[str], message: str
    ) -> AsyncIterator[StreamEvent]:
        """Spawn the subprocess and yield parsed stream events."""
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=PIPE,
            stdout=PIPE,
            stderr=PIPE,
            cwd=self._cwd,
        )

        assert proc.stdin is not None
        assert proc.stdout is not None
        assert proc.stderr is not None

        # Send the user message via stdin and close to signal EOF
        proc.stdin.write(message.encode())
        await proc.stdin.drain()
        proc.stdin.close()

        session_captured = False

        async for raw_line in proc.stdout:
            line = raw_line.decode(errors="replace").strip()
            if not line:
                continue

            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                log.debug("send_message: non-JSON stdout line: %r", line[:200])
                continue

            msg_type = obj.get("type", "")

            # ------------------------------------------------------------------
            # system message — captures the initial session_id
            # ------------------------------------------------------------------
            if msg_type == "system":
                subtype = obj.get("subtype", "")
                if subtype == "init":
                    sid = obj.get("session_id") or ""
                    if sid and not session_captured:
                        self._session_id = sid
                        session_captured = True
                        self._repo.update_session_id(self._orchestrator_id, sid)
                        log.info(
                            "send_message: new session_id=%s for orchestrator_id=%s",
                            sid,
                            self._orchestrator_id,
                        )
                continue

            # ------------------------------------------------------------------
            # assistant message — extract and yield content blocks
            # ------------------------------------------------------------------
            if msg_type == "assistant":
                content_raw = obj.get("message", {}) or {}
                # JSONL format: {type: "assistant", message: {role: "assistant", content: [...]}}
                if isinstance(content_raw, dict):
                    content_list = content_raw.get("content", [])
                else:
                    content_list = []

                for block in _parse_content_blocks(content_list):
                    yield block
                continue

            # ------------------------------------------------------------------
            # result message — final; extract usage and persist
            # ------------------------------------------------------------------
            if msg_type == "result":
                subtype = obj.get("subtype", "error")
                usage = obj.get("usage") or {}

                # session_id may also appear on the result line
                result_sid = obj.get("session_id")
                if result_sid and not session_captured:
                    self._session_id = result_sid
                    self._repo.update_session_id(self._orchestrator_id, result_sid)
                    session_captured = True

                input_toks = usage.get("input_tokens", 0)
                output_toks = usage.get("output_tokens", 0)
                cache_read = usage.get("cache_read_input_tokens", 0)
                cache_write = usage.get("cache_creation_input_tokens", 0)
                cost_usd = obj.get("cost_usd") or obj.get("total_cost_usd")

                # Accumulate into DB
                self._repo.accumulate_tokens(
                    self._orchestrator_id,
                    input_tokens=input_toks,
                    output_tokens=output_toks,
                    cost_usd=float(cost_usd) if cost_usd is not None else 0.0,
                )

                result_event = ResultMessage(
                    subtype=subtype,
                    session_id=self._session_id,
                    result=obj.get("result"),
                    error=obj.get("error"),
                    input_tokens=input_toks,
                    output_tokens=output_toks,
                    cache_read_input_tokens=cache_read,
                    cache_creation_input_tokens=cache_write,
                    cost_usd=float(cost_usd) if cost_usd is not None else None,
                )
                yield result_event
                continue

        # Collect stderr for error logging (non-blocking drain)
        stderr_bytes = await proc.stderr.read()
        await proc.wait()

        if proc.returncode != 0:
            stderr_text = stderr_bytes.decode(errors="replace").strip()
            log.warning(
                "send_message: claude CLI exited with code %d. stderr: %s",
                proc.returncode,
                stderr_text[:500],
            )
