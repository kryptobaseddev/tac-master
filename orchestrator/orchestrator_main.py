#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "aiohttp>=3.9",
#   "pyyaml>=6.0",
#   "python-dotenv>=1.0",
#   "httpx>=0.27",
# ]
# ///
"""Python-side HTTP server exposing OrchestratorService streaming endpoints.

Runs on port 4001 and provides:
  - POST /send_chat: Accept user message, trigger orchestrator processing
  - GET /api/chat/history: Retrieve chat history for hydration

Streams responses via WebSocket connection to the dashboard /events endpoint
(fire-and-forget — orchestrator does not wait for delivery confirmation).

This is the T106 implementation of the Python orchestrator bridge.

Usage:
    uv run orchestrator/orchestrator_main.py              # foreground, daemon
    ORCHESTRATOR_PORT=4001 uv run orchestrator/orchestrator_main.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

# Make orchestrator package importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiohttp import web

from .orchestrator_service import OrchestratorService

log = logging.getLogger(__name__)

# Global orchestrator instance (one per process)
_orchestrator: Optional[OrchestratorService] = None


# ============================================================================
# Setup and initialization
# ============================================================================


def init_orchestrator(
    db_path: Optional[str] = None,
    dashboard_url: str = "http://localhost:4000",
    system_prompt_path: Optional[str] = None,
    working_dir: Optional[str] = None,
    model: str = "sonnet",
) -> OrchestratorService:
    """Initialize the global OrchestratorService singleton.

    Parameters
    ----------
    db_path:
        Path to tac_master.sqlite. If None, searches standard locations.
    dashboard_url:
        Base URL of the Bun dashboard (default http://localhost:4000).
        Events are POSTed to {dashboard_url}/events.
    system_prompt_path:
        Path to the orchestrator_system.md template. If None, uses default.
    working_dir:
        CWD for the claude CLI subprocess. If None, defaults to tac-master root.
    model:
        Claude model alias (default "sonnet").

    Returns
    -------
    OrchestratorService
        The initialized orchestrator instance.
    """
    global _orchestrator

    if _orchestrator is not None:
        return _orchestrator

    # Resolve db_path
    if db_path is None:
        candidates = [
            Path(__file__).parent.parent / "tac_master.sqlite",
            Path("/srv/tac-master/tac_master.sqlite"),
            Path("/var/lib/tac-master/tac_master.sqlite"),
        ]
        for candidate in candidates:
            if candidate.exists():
                db_path = str(candidate)
                break
        if db_path is None:
            raise RuntimeError(
                "Could not find tac_master.sqlite. "
                "Provide db_path or ensure it exists in a standard location."
            )

    # Resolve working_dir
    if working_dir is None:
        working_dir = str(Path(__file__).parent.parent)

    log.info(
        "Initializing OrchestratorService (db=%s, model=%s, cwd=%s)",
        db_path,
        model,
        working_dir,
    )

    _orchestrator = OrchestratorService(
        db_path=db_path,
        dashboard_url=dashboard_url,
        system_prompt_path=system_prompt_path,
        working_dir=working_dir,
        model=model,
    )

    return _orchestrator


# ============================================================================
# HTTP Request Handlers
# ============================================================================


async def handle_send_chat(request: web.Request) -> web.Response:
    """POST /send_chat — Forward user message to orchestrator and stream response.

    Request body:
      {
        "message": "user message text",
        "orchestrator_agent_id": "orch-xxxxx" (optional, for logging)
      }

    Returns 200 immediately (fire-and-forget). The orchestrator streams
    its response asynchronously via POST /events to the dashboard.

    Returns 503 if orchestrator is busy processing another message.

    Error responses:
      - 400: Missing or empty message field
      - 503: Orchestrator is busy
      - 500: Internal error
    """
    if _orchestrator is None:
        return web.json_response(
            {"error": "Orchestrator not initialized"},
            status=500,
        )

    try:
        body = await request.json()
    except Exception as exc:
        log.warning("handle_send_chat: failed to parse JSON: %s", exc)
        return web.json_response(
            {"error": "Invalid JSON"},
            status=400,
        )

    message = body.get("message", "").strip()
    if not message:
        return web.json_response(
            {"error": "message is required and must not be empty"},
            status=400,
        )

    # Check if orchestrator is busy
    if _orchestrator.is_busy:
        log.warning(
            "handle_send_chat: orchestrator is busy; returning 503"
        )
        return web.json_response(
            {"error": "Orchestrator is busy processing another message"},
            status=503,
        )

    # Fire-and-forget: spawn the processing task and return immediately
    asyncio.create_task(_process_message_async(message))

    return web.json_response(
        {
            "ok": True,
            "message": "Message queued for processing",
        },
        status=200,
    )


async def handle_chat_history(request: web.Request) -> web.Response:
    """GET /api/chat/history — Retrieve chat history for the active orchestrator.

    Query parameters:
      - limit (default 50): Maximum number of messages to return

    Returns:
      {
        "messages": [
          {
            "id": "msg-xxxx",
            "orchestrator_agent_id": "orch-xxxx",
            "sender_type": "user" | "orchestrator",
            "message": "text",
            "metadata": {},
            "created_at": 1234567890,
            "updated_at": 1234567890
          },
          ...
        ]
      }

    Error responses:
      - 500: Failed to retrieve history
    """
    if _orchestrator is None:
        return web.json_response(
            {"error": "Orchestrator not initialized"},
            status=500,
        )

    try:
        limit = int(request.rel_url.query.get("limit", 50))
        limit = max(1, min(limit, 1000))  # Clamp to [1, 1000]
    except ValueError:
        limit = 50

    try:
        messages = await _orchestrator.load_chat_history(limit=limit)
        return web.json_response(
            {
                "messages": messages,
                "count": len(messages),
            },
            status=200,
        )
    except Exception as exc:
        log.error("handle_chat_history: failed to load history: %s", exc)
        return web.json_response(
            {"error": "Failed to retrieve chat history"},
            status=500,
        )


async def handle_health(request: web.Request) -> web.Response:
    """GET /health — Service health check."""
    if _orchestrator is None:
        return web.json_response(
            {
                "status": "starting",
                "service": "orchestrator",
                "is_busy": False,
            },
            status=503,
        )

    return web.json_response(
        {
            "status": "ok",
            "service": "orchestrator",
            "is_busy": _orchestrator.is_busy,
        },
        status=200,
    )


# ============================================================================
# Background task: Consume orchestrator stream and POST to dashboard
# ============================================================================


async def _process_message_async(message: str) -> None:
    """Process a user message through the orchestrator async stream.

    Consumes the OrchestratorService.process_user_message() async iterator
    and logs/broadcasts any exceptions (never raises to callers).

    This runs in a detached task so the HTTP handler can return immediately.
    """
    if _orchestrator is None:
        log.error("_process_message_async: orchestrator not initialized")
        return

    try:
        log.info("_process_message_async: starting for message: %s", message[:80])

        # Iterate over the streaming response. OrchestratorService yields
        # TextBlock, ThinkingBlock, ToolUseBlock, ResultMessage events.
        # Each event is independently POSTed to the dashboard /events endpoint
        # by OrchestratorService._post_event() (fire-and-forget).
        event_count = 0
        async for event in _orchestrator.process_user_message(message):
            event_count += 1
            # Just consume the stream — OrchestratorService handles all
            # event broadcasting internally.
            log.debug("_process_message_async: received event %d: %s", event_count, type(event).__name__)

        log.info(
            "_process_message_async: completed (processed %d events)",
            event_count,
        )

    except Exception as exc:
        log.error("_process_message_async: exception in stream: %s", exc, exc_info=True)


# ============================================================================
# Logging setup
# ============================================================================


def setup_logging(level: str = "INFO") -> None:
    """Configure logging for the orchestrator service."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    log.info("Logging configured at %s level", level)


# ============================================================================
# Main entry point
# ============================================================================


async def main(
    port: int = 4001,
    db_path: Optional[str] = None,
    dashboard_url: str = "http://localhost:4000",
    system_prompt_path: Optional[str] = None,
    working_dir: Optional[str] = None,
    model: str = "sonnet",
) -> None:
    """Run the orchestrator HTTP server.

    Parameters
    ----------
    port:
        HTTP server port (default 4001).
    db_path:
        Path to tac_master.sqlite. If None, searches standard locations.
    dashboard_url:
        Base URL of the Bun dashboard (default http://localhost:4000).
    system_prompt_path:
        Path to the orchestrator_system.md template. If None, uses default.
    working_dir:
        CWD for the claude CLI subprocess. If None, defaults to tac-master root.
    model:
        Claude model alias (default "sonnet").
    """
    setup_logging(os.environ.get("LOG_LEVEL", "INFO"))

    # Initialize orchestrator
    orchestrator = init_orchestrator(
        db_path=db_path,
        dashboard_url=dashboard_url,
        system_prompt_path=system_prompt_path,
        working_dir=working_dir,
        model=model,
    )

    # Create aiohttp app
    app = web.Application()

    # Register routes
    app.router.add_post("/send_chat", handle_send_chat)
    app.router.add_get("/api/chat/history", handle_chat_history)
    app.router.add_get("/health", handle_health)

    # Create and start runner
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "localhost", port)
    await site.start()

    log.info("Orchestrator server listening on http://localhost:%d", port)

    # Keep the server running
    try:
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        log.info("Orchestrator server shutting down...")
        await runner.cleanup()
        if orchestrator:
            orchestrator.close()


if __name__ == "__main__":
    # Allow environment variable overrides
    port = int(os.environ.get("ORCHESTRATOR_PORT", 4001))
    db_path = os.environ.get("TAC_MASTER_DB")
    dashboard_url = os.environ.get("DASHBOARD_URL", "http://localhost:4000")
    system_prompt_path = os.environ.get("SYSTEM_PROMPT_PATH")
    working_dir = os.environ.get("TAC_MASTER_CWD")
    model = os.environ.get("ORCHESTRATOR_MODEL", "sonnet")

    try:
        asyncio.run(
            main(
                port=port,
                db_path=db_path,
                dashboard_url=dashboard_url,
                system_prompt_path=system_prompt_path,
                working_dir=working_dir,
                model=model,
            )
        )
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as exc:
        log.error("Fatal error: %s", exc, exc_info=True)
        sys.exit(1)
