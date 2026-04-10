"""OrchestratorService — three-phase streaming with Claude SDK session management.

Ports the core OrchestratorService from orchestrator_3_stream to tac-master.
Replaces WebSocketManager/asyncpg with HTTP POST to dashboard /events endpoint
and the SQLite-backed OrchestratorAgentModel + db_repositories layer.

Three-phase execution pattern:
  Phase 1: Insert user message to chat_messages, POST event to dashboard.
  Phase 2: Async streaming loop over ClaudeSDKClient.send_message():
           TextBlock  → save chat message + POST text_block event
           ThinkingBlock → save system_log + POST thinking_block event
           ToolUseBlock  → save system_log + POST tool_use_block event
           ResultMessage → extract session_id, token usage, cost
  Phase 3: Update session_id (when new) and accumulate costs in orchestrator_agents.

Session resumption:
  If started with an existing session_id, passes ``--resume`` to ClaudeSDKClient
  and skips overwriting the session_id in the DB after Phase 3
  (started_with_session flag).

Interrupt support:
  interrupt() acquires _execution_lock and calls active_client process interrupt.
  Subsequent process_user_message calls work normally.

Event broadcasting:
  HTTP POST to http://localhost:4000/events (same endpoint used by hooks).
  Non-blocking — failures are logged but never raise to callers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import httpx

from .claude_sdk_client import (
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
)
from .orchestrator_agent import OrchestratorAgentModel
from .prompt_builder import build_system_prompt

log = logging.getLogger(__name__)


class OrchestratorService:
    """Persistent Claude SDK session manager for tac-master.

    One instance per process.  Owns a single OrchestratorAgentModel (SQLite
    connection) and a single ClaudeSDKClient.  All public methods are safe to
    call from an asyncio event loop.

    Parameters
    ----------
    db_path:
        Absolute path to tac_master.sqlite.
    system_prompt_path:
        Path to the orchestrator_system.md template file.  Passed to
        prompt_builder.build_system_prompt().  May be None to use the
        default path resolved by prompt_builder.
    dashboard_url:
        Base URL of the Bun dashboard server (default http://localhost:4000).
        Events are POSTed to ``{dashboard_url}/events``.
    working_dir:
        CWD for the claude CLI subprocess.  Defaults to the tac-master root
        (parent of the orchestrator/ package directory).
    session_id:
        Claude SDK session_id to resume.  When provided, ``--resume`` is
        passed on the first send_message call and the DB update in Phase 3
        is skipped (started_with_session=True).
    state_store:
        Optional StateStore instance shared with daemon.py.  Passed to
        prompt_builder for {{ACTIVE_RUNS}} injection.  If None, active-runs
        section uses a graceful fallback.
    model:
        Claude model alias (default ``"sonnet"``).
    """

    def __init__(
        self,
        db_path: str,
        system_prompt_path: Optional[str] = None,
        dashboard_url: str = "http://localhost:4000",
        working_dir: Optional[str] = None,
        session_id: Optional[str] = None,
        state_store: Any = None,
        model: str = "sonnet",
    ) -> None:
        self._db_path = db_path
        self._dashboard_url = dashboard_url.rstrip("/")
        self._events_url = f"{self._dashboard_url}/events"

        # Working directory for the claude subprocess
        if working_dir is None:
            # Default: tac-master root (two levels up from orchestrator/)
            working_dir = str(Path(__file__).parent.parent)
        self._working_dir = working_dir

        self._state_store = state_store
        self._model = model

        # Resolve system prompt
        if system_prompt_path is not None:
            from .prompt_builder import PROMPT_TEMPLATE_PATH
            prompt_kwargs: dict[str, Any] = {
                "prompt_path": Path(system_prompt_path),
                "state_store": state_store,
            }
        else:
            prompt_kwargs = {"state_store": state_store}

        try:
            self._system_prompt: str = build_system_prompt(**prompt_kwargs)
        except FileNotFoundError as exc:
            log.warning("System prompt file not found (%s); using fallback.", exc)
            self._system_prompt = (
                "You are a helpful orchestrator agent that manages tac-master, "
                "a system that dispatches AI Developer Workflows (ADWs) to GitHub repositories."
            )

        # OrchestratorAgentModel — owns the SQLite connection
        self._agent_model = OrchestratorAgentModel(db_path)

        # Resolve or create the orchestrator_agents row
        existing = self._agent_model.get_active()
        if existing is not None:
            self._orchestrator_id: str = existing["id"]
            log.info("OrchestratorService: resuming active agent row %s", self._orchestrator_id)
        else:
            self._orchestrator_id = self._agent_model.spawn_session(
                system_prompt=self._system_prompt,
                working_dir=self._working_dir,
            )
            log.info("OrchestratorService: created new agent row %s", self._orchestrator_id)

        # Session resumption state
        self.session_id: Optional[str] = session_id
        self.started_with_session: bool = session_id is not None

        if self.session_id:
            log.info(
                "OrchestratorService: will resume Claude SDK session %s…",
                self.session_id[:20],
            )

        # Build ClaudeSDKClient (lazy session init — no subprocess until first message)
        # We need a minimal state_store adapter for ClaudeSDKClient's OrchestratorAgentRepo.
        # ClaudeSDKClient.OrchestratorAgentRepo.get_session_id() calls state_store.conn().
        # We provide a thin adapter that proxies .conn() to our OrchestratorAgentModel._conn.
        _store_adapter = _SQLiteConnAdapter(self._agent_model)

        self._client = ClaudeSDKClient(
            state_store=_store_adapter,
            orchestrator_id=self._orchestrator_id,
            cwd=self._working_dir,
            system_prompt=self._system_prompt,
            model=self._model,
        )

        # If resuming, hydrate the client's in-memory session_id
        if self.session_id:
            self._client.resume_session(self.session_id)

        # Interrupt / execution state
        self._execution_lock: asyncio.Lock = asyncio.Lock()
        self.is_executing: bool = False
        self._active_proc: Optional[asyncio.subprocess.Process] = None  # type: ignore[type-arg]

        log.info(
            "OrchestratorService initialised (orchestrator_id=%s, model=%s, cwd=%s)",
            self._orchestrator_id,
            self._model,
            self._working_dir,
        )

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    @property
    def is_busy(self) -> bool:
        """True while process_user_message is actively streaming a response."""
        return self.is_executing

    async def process_user_message(
        self,
        message: str,
        session_id: Optional[str] = None,
    ) -> AsyncIterator[Any]:
        """Process a user message through the three-phase streaming pattern.

        Yields each stream event (TextBlock, ThinkingBlock, ToolUseBlock,
        ResultMessage) as it arrives from the Claude SDK so callers can relay
        chunks to HTTP clients in real time.

        Parameters
        ----------
        message:
            The user's text message.
        session_id:
            Ignored at call time (session state is maintained internally).
            Accepted for API compatibility.

        Yields
        ------
        TextBlock | ThinkingBlock | ToolUseBlock | ResultMessage
        """
        return self._run_phases(message)

    async def load_chat_history(
        self,
        session_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return chat history for the active orchestrator session.

        Parameters
        ----------
        session_id:
            Ignored — history is loaded for the active orchestrator_id.
        limit:
            Maximum number of messages to return (default 50).

        Returns
        -------
        list[dict]
            Chat messages ordered by created_at ASC.
        """
        from .db_repositories import ChatMessageRepo

        with self._agent_model._conn as conn:
            messages = ChatMessageRepo.get_history(
                conn,
                orchestrator_agent_id=self._orchestrator_id,
                limit=limit,
            )

        return messages

    async def interrupt(self) -> None:
        """Interrupt the currently executing Claude SDK subprocess.

        Acquires the execution lock and terminates the active subprocess (if
        any).  The next call to process_user_message will start a fresh
        send_message call — no additional setup is required.

        Safe to call even when nothing is executing (no-op).
        """
        async with self._execution_lock:
            if not self.is_executing:
                log.debug("interrupt(): not currently executing — no-op")
                return

            if self._active_proc is not None:
                try:
                    self._active_proc.terminate()
                    log.info("interrupt(): terminated active claude subprocess")
                except ProcessLookupError:
                    log.debug("interrupt(): process already exited")
                except Exception as exc:
                    log.warning("interrupt(): terminate failed: %s", exc)
            else:
                log.debug("interrupt(): is_executing=True but _active_proc is None")

    def close(self) -> None:
        """Release the SQLite connection held by OrchestratorAgentModel."""
        self._agent_model.close()

    # -------------------------------------------------------------------------
    # Internal — three-phase execution
    # -------------------------------------------------------------------------

    async def _run_phases(self, message: str) -> AsyncIterator[Any]:
        """Async generator implementing the three-phase streaming loop.

        Callers receive this as an async iterator and can ``async for`` over it.
        """

        orchestrator_id = self._orchestrator_id

        # ═══════════════════════════════════════════════════════════════════
        # PHASE 1: Log user message + POST /events
        # ═══════════════════════════════════════════════════════════════════

        try:
            chat_row = self._agent_model.record_response(
                agent_id=orchestrator_id,
                message=message,
                sender_type="user",
            )
        except Exception as exc:
            log.error("Phase 1: failed to save user message: %s", exc)
            raise

        # Fire-and-forget event POST
        asyncio.create_task(
            self._post_event(
                {
                    "type": "orchestrator_chat",
                    "message": {
                        "id": chat_row["id"],
                        "orchestrator_agent_id": orchestrator_id,
                        "sender_type": "user",
                        "receiver_type": "orchestrator",
                        "message": message,
                        "metadata": {},
                        "timestamp": _iso_now(),
                    },
                }
            )
        )

        # ═══════════════════════════════════════════════════════════════════
        # PHASE 2: Async streaming loop
        # ═══════════════════════════════════════════════════════════════════

        response_text: list[str] = []
        tools_used: list[str] = []
        result_msg: Optional[ResultMessage] = None

        async with self._execution_lock:
            self.is_executing = True

        try:
            async for event in self._client.send_message(message):
                # Store the subprocess handle for interrupt() support
                if self._active_proc is None and hasattr(self._client, "_proc"):
                    self._active_proc = getattr(self._client, "_proc", None)

                if isinstance(event, TextBlock):
                    response_text.append(event.text)

                    try:
                        msg_row = self._agent_model.record_response(
                            agent_id=orchestrator_id,
                            message=event.text,
                            sender_type="orchestrator",
                        )
                    except Exception as exc:
                        log.error("Phase 2: failed to save TextBlock: %s", exc)
                        msg_row = {"id": "error"}

                    asyncio.create_task(
                        self._post_event(
                            {
                                "type": "text_block",
                                "data": {
                                    "id": msg_row.get("id", ""),
                                    "orchestrator_agent_id": orchestrator_id,
                                    "sender_type": "orchestrator",
                                    "receiver_type": "user",
                                    "message": event.text,
                                    "metadata": {"type": "text_chunk"},
                                    "timestamp": _iso_now(),
                                },
                            }
                        )
                    )
                    yield event

                elif isinstance(event, ThinkingBlock):
                    try:
                        log_row = self._agent_model.log_block(
                            agent_id=orchestrator_id,
                            log_type="thinking",
                            content=f"Orchestrator thinking: {event.thinking[:100]}…",
                            payload={
                                "type": "thinking_block",
                                "thinking": event.thinking,
                                "orchestrator_agent_id": orchestrator_id,
                            },
                        )
                    except Exception as exc:
                        log.error("Phase 2: failed to save ThinkingBlock: %s", exc)
                        log_row = {"id": "error"}

                    asyncio.create_task(
                        self._post_event(
                            {
                                "type": "thinking_block",
                                "data": {
                                    "id": log_row.get("id", ""),
                                    "orchestrator_agent_id": orchestrator_id,
                                    "thinking": event.thinking,
                                    "timestamp": _iso_now(),
                                },
                            }
                        )
                    )
                    yield event

                elif isinstance(event, ToolUseBlock):
                    tools_used.append(event.name)

                    try:
                        log_row = self._agent_model.log_block(
                            agent_id=orchestrator_id,
                            log_type="tool_use",
                            content=f"Orchestrator using tool: {event.name}",
                            payload={
                                "type": "tool_use_block",
                                "tool_name": event.name,
                                "tool_input": event.input,
                                "tool_use_id": event.id,
                                "orchestrator_agent_id": orchestrator_id,
                            },
                        )
                    except Exception as exc:
                        log.error("Phase 2: failed to save ToolUseBlock: %s", exc)
                        log_row = {"id": "error"}

                    asyncio.create_task(
                        self._post_event(
                            {
                                "type": "tool_use_block",
                                "data": {
                                    "id": log_row.get("id", ""),
                                    "orchestrator_agent_id": orchestrator_id,
                                    "tool_name": event.name,
                                    "tool_input": event.input,
                                    "tool_use_id": event.id,
                                    "timestamp": _iso_now(),
                                },
                            }
                        )
                    )
                    yield event

                elif isinstance(event, ResultMessage):
                    result_msg = event
                    yield event

        except Exception as exc:
            log.error("Phase 2: streaming loop failed: %s", exc)
            raise

        finally:
            async with self._execution_lock:
                self.is_executing = False
                self._active_proc = None

        # ═══════════════════════════════════════════════════════════════════
        # PHASE 3: Update session_id and costs
        # ═══════════════════════════════════════════════════════════════════

        if result_msg is not None:
            final_session_id = result_msg.session_id
            input_tokens = result_msg.input_tokens or 0
            output_tokens = result_msg.output_tokens or 0
            cost_usd = float(result_msg.cost_usd or 0.0)

            # Persist new session_id only when this was a brand-new session
            if final_session_id:
                self.session_id = final_session_id
                if not self.started_with_session:
                    try:
                        from .db_repositories import OrchestratorAgentRepo
                        with self._agent_model._conn as conn:
                            OrchestratorAgentRepo.update_session(
                                conn, orchestrator_id, final_session_id
                            )
                        log.info(
                            "Phase 3: persisted new session_id %s…",
                            final_session_id[:20],
                        )
                    except Exception as exc:
                        log.error("Phase 3: failed to persist session_id: %s", exc)
                else:
                    log.debug("Phase 3: skipping session_id DB update (resumed session)")

            # Accumulate costs (ClaudeSDKClient already accumulates via its own repo,
            # but we also use OrchestratorAgentModel.record_response which tracks separately;
            # post a broadcast so the dashboard reflects the latest totals)
            asyncio.create_task(
                self._post_event(
                    {
                        "type": "orchestrator_status",
                        "data": {
                            "id": orchestrator_id,
                            "status": "idle",
                            "session_id": final_session_id,
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                            "cost_usd": cost_usd,
                            "tools_used": tools_used,
                            "timestamp": _iso_now(),
                        },
                    }
                )
            )

        # Stream-complete signal so the dashboard can clear the typing indicator
        asyncio.create_task(
            self._post_event(
                {
                    "type": "chat_stream_complete",
                    "data": {
                        "orchestrator_agent_id": orchestrator_id,
                        "timestamp": _iso_now(),
                    },
                }
            )
        )

    # -------------------------------------------------------------------------
    # Internal — HTTP event broadcasting
    # -------------------------------------------------------------------------

    async def _post_event(self, payload: dict[str, Any]) -> None:
        """POST *payload* as JSON to the dashboard /events endpoint.

        Non-blocking fire-and-forget.  Failures are logged at WARNING level
        and never propagate to callers.
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as http:
                resp = await http.post(self._events_url, json=payload)
                if resp.status_code >= 400:
                    log.warning(
                        "_post_event: dashboard returned HTTP %d for event type=%s",
                        resp.status_code,
                        payload.get("type"),
                    )
        except httpx.ConnectError:
            log.debug(
                "_post_event: dashboard not reachable at %s (event type=%s)",
                self._events_url,
                payload.get("type"),
            )
        except Exception as exc:
            log.warning("_post_event: unexpected error posting event: %s", exc)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _iso_now() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


class _SQLiteConnAdapter:
    """Thin adapter that exposes a StateStore-compatible .conn() context manager.

    ClaudeSDKClient's internal OrchestratorAgentRepo calls
    ``state_store.conn()`` as a context manager.  This adapter proxies the
    call to the OrchestratorAgentModel's raw sqlite3.Connection so we don't
    need a second StateStore instance.
    """

    def __init__(self, agent_model: OrchestratorAgentModel) -> None:
        self._model = agent_model

    def conn(self):
        """Return the underlying sqlite3.Connection as a context manager."""
        return _RawConnContextManager(self._model._conn)


class _RawConnContextManager:
    """Wraps a raw sqlite3.Connection to behave like StateStore.conn().

    On exit, commits (on success) or rolls back (on exception) — matching
    the StateStore.conn() contract expected by ClaudeSDKClient internals.
    """

    def __init__(self, raw_conn) -> None:
        self._conn = raw_conn

    def __enter__(self):
        return self._conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            try:
                self._conn.commit()
            except Exception as exc:
                log.warning("_RawConnContextManager: commit failed: %s", exc)
        else:
            try:
                self._conn.rollback()
            except Exception:
                pass
        return False  # do not suppress exceptions
