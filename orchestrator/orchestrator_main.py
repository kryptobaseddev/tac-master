#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "fastapi>=0.110",
#   "uvicorn>=0.30",
#   "pyyaml>=6.0",
#   "python-dotenv>=1.0",
#   "httpx>=0.27",
# ]
# ///

"""tac-orchestrator HTTP server entry point.

Minimal async HTTP server (FastAPI/uvicorn on port 4001) that acts as the
entry point for the tac-orchestrator systemd service.

Startup sequence:
  1. Load tac-master config to resolve the SQLite path.
  2. Read active session_id from SQLite via OrchestratorAgentModel.get_active().
  3. Initialise OrchestratorService (with session resumption when an active
     session_id is found).
  4. Start uvicorn on port 4001.

Endpoints:
  POST /chat         — Accepts {message, orchestrator_id?}, fires
                       OrchestratorService.process_user_message() as an
                       asyncio background task, returns 202 immediately.
  GET  /status       — Returns {status, session_id, cost_usd,
                       input_tokens, output_tokens}.
  POST /interrupt    — Calls OrchestratorService.interrupt(), returns 200.

SIGTERM handling:
  The SIGTERM signal is caught and converted to a clean FastAPI/uvicorn
  shutdown.  The lifespan shutdown hook waits for any active execution to
  complete (max 30s), then logs "Graceful shutdown complete" and exits.

Usage:
    uv run python -m orchestrator.orchestrator_main
    TAC_MASTER_HOME=/path/to/tac-master uv run python -m orchestrator.orchestrator_main
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path bootstrap — make tac-master root importable when invoked as __main__
# ---------------------------------------------------------------------------

_ORCHESTRATOR_DIR = Path(__file__).resolve().parent
_TAC_MASTER_ROOT = _ORCHESTRATOR_DIR.parent
if str(_TAC_MASTER_ROOT) not in sys.path:
    sys.path.insert(0, str(_TAC_MASTER_ROOT))

# deferred imports after path is set up
import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from orchestrator.config import load_config  # noqa: E402
from orchestrator.orchestrator_agent import OrchestratorAgentModel  # noqa: E402
from orchestrator.orchestrator_service import OrchestratorService  # noqa: E402

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("orchestrator_main")

# ---------------------------------------------------------------------------
# Global service state (set during lifespan startup)
# ---------------------------------------------------------------------------

_service: OrchestratorService | None = None

# uvicorn Server instance (set in main()) — used by the SIGTERM handler to
# request a graceful shutdown without killing the process immediately.
_uvicorn_server: uvicorn.Server | None = None

# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str
    orchestrator_id: str | None = None


# ---------------------------------------------------------------------------
# Lifespan — startup and shutdown
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _lifespan(app: FastAPI):  # noqa: ARG001
    """FastAPI lifespan context manager — startup then teardown."""
    global _service

    # ── Startup ──────────────────────────────────────────────────────────
    log.info("tac-orchestrator starting on port 4001")

    # Resolve db_path from tac-master config
    db_path: str
    try:
        cfg = load_config()
        db_path = str(cfg.sqlite_path)
        log.info("Using SQLite at %s", db_path)
    except Exception as exc:
        log.warning(
            "Config load failed (%s) — falling back to default SQLite path", exc
        )
        db_path = str(_TAC_MASTER_ROOT / "state" / "tac_master.sqlite")

    # Read active session_id for resumption
    session_id: str | None = None
    try:
        probe = OrchestratorAgentModel(db_path)
        try:
            active = probe.get_active()
        finally:
            probe.close()

        if active is not None:
            session_id = active.get("session_id")
            if session_id:
                short = session_id[:20] if len(session_id) > 20 else session_id
                log.info("Resuming existing Claude SDK session %s…", short)
            else:
                log.info(
                    "Active orchestrator row found (id=%s) but no session_id — fresh session",
                    active.get("id"),
                )
        else:
            log.info("No active orchestrator row found — starting fresh session")
    except Exception as exc:
        log.warning(
            "Could not read active session from DB (%s) — starting fresh", exc
        )

    # Build OrchestratorService
    try:
        _service = OrchestratorService(
            db_path=db_path,
            dashboard_url=os.getenv("DASHBOARD_URL", "http://localhost:4000"),
            session_id=session_id,
            model=os.getenv("ORCHESTRATOR_MODEL", "sonnet"),
        )
        log.info(
            "OrchestratorService ready (session_id=%s)",
            session_id or "<new>",
        )
    except Exception as exc:
        log.error("Failed to initialise OrchestratorService: %s", exc, exc_info=True)
        raise

    yield  # hand control to uvicorn

    # ── Shutdown ──────────────────────────────────────────────────────────
    log.info("tac-orchestrator lifespan: waiting for active execution to finish…")

    if _service is not None and _service.is_busy:
        try:
            deadline = 30.0
            elapsed = 0.0
            step = 0.5
            while _service.is_busy and elapsed < deadline:
                await asyncio.sleep(step)
                elapsed += step

            if _service.is_busy:
                log.warning(
                    "Execution still active after %.0fs — proceeding with shutdown",
                    deadline,
                )
            else:
                log.info("Active execution finished cleanly (waited %.1fs)", elapsed)
        except Exception as exc:
            log.warning("Error while waiting for execution to complete: %s", exc)

    if _service is not None:
        try:
            _service.close()
        except Exception as exc:
            log.warning("OrchestratorService.close() raised: %s", exc)
        finally:
            _service = None

    log.info("Graceful shutdown complete")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="tac-orchestrator",
    version="0.1",
    description="Async HTTP entry point for the tac-master orchestrator service",
    lifespan=_lifespan,
)

# ---------------------------------------------------------------------------
# Endpoint: POST /chat
# ---------------------------------------------------------------------------


@app.post("/chat", status_code=202)
async def post_chat(request: ChatRequest) -> JSONResponse:
    """Receive a user message and trigger OrchestratorService processing.

    Fires OrchestratorService.process_user_message() as an asyncio background
    task and returns 202 immediately.  The service streams responses back to
    the dashboard via HTTP POST to the /events endpoint.

    Body:
        message         (str, required)  — the user's chat message
        orchestrator_id (str, optional)  — ignored; kept for API compatibility

    Returns:
        202 {"status": "accepted"} on success
        400 if message is empty
        503 if service is not initialised
    """
    if _service is None:
        return JSONResponse(
            {"error": "OrchestratorService not initialised"},
            status_code=503,
        )

    message = request.message.strip()
    if not message:
        return JSONResponse(
            {"error": "message must not be empty"},
            status_code=400,
        )

    async def _drain() -> None:
        """Consume the async generator returned by process_user_message.

        OrchestratorService._run_phases() POSTs events to the dashboard
        internally, so we just drain the iterator to drive the coroutine.
        """
        try:
            result = await _service.process_user_message(message)
            async for _ in result:
                pass  # events are broadcast inside _run_phases
        except Exception as exc:
            log.error("Background /chat task failed: %s", exc, exc_info=True)

    asyncio.create_task(_drain())
    return JSONResponse({"status": "accepted"}, status_code=202)


# ---------------------------------------------------------------------------
# Endpoint: GET /status
# ---------------------------------------------------------------------------


@app.get("/status")
async def get_status() -> JSONResponse:
    """Return the current orchestrator status and accumulated metrics.

    Response body:
        status        — "idle" | "executing" | "unavailable"
        session_id    — Claude SDK session id (or null)
        cost_usd      — accumulated USD cost for this session
        input_tokens  — total input tokens consumed
        output_tokens — total output tokens generated

    Reads cost/token totals live from the orchestrator_agents table.
    """
    if _service is None:
        return JSONResponse(
            {
                "status": "unavailable",
                "session_id": None,
                "cost_usd": 0.0,
                "input_tokens": 0,
                "output_tokens": 0,
            }
        )

    status = "executing" if _service.is_busy else "idle"
    session_id = _service.session_id

    cost_usd = 0.0
    input_tokens = 0
    output_tokens = 0
    try:
        from orchestrator.db_repositories import OrchestratorAgentRepo

        with _service._agent_model._conn as conn:
            row = OrchestratorAgentRepo.get(conn, _service._orchestrator_id)
            if row is not None:
                cost_usd = float(row.get("total_cost") or 0.0)
                input_tokens = int(row.get("input_tokens") or 0)
                output_tokens = int(row.get("output_tokens") or 0)
    except Exception as exc:
        log.warning("Could not read cost metrics from DB: %s", exc)

    return JSONResponse(
        {
            "status": status,
            "session_id": session_id,
            "cost_usd": cost_usd,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
    )


# ---------------------------------------------------------------------------
# Endpoint: POST /interrupt
# ---------------------------------------------------------------------------


@app.post("/interrupt")
async def post_interrupt() -> JSONResponse:
    """Interrupt the currently executing Claude SDK subprocess.

    Safe to call when nothing is executing (no-op, returns 200).

    Returns:
        200 {"status": "interrupted"} always (unless service unavailable)
        503 if service is not initialised
    """
    if _service is None:
        return JSONResponse(
            {"error": "OrchestratorService not initialised"},
            status_code=503,
        )

    await _service.interrupt()
    return JSONResponse({"status": "interrupted"})


# ---------------------------------------------------------------------------
# SIGTERM handler
# ---------------------------------------------------------------------------


def _install_sigterm_handler() -> None:
    """Install a SIGTERM handler that initiates a clean uvicorn shutdown.

    uvicorn monitors its own Server.should_exit flag.  We set it here so
    that the server begins draining connections and triggers the lifespan
    teardown (which waits for active executions before logging the final
    "Graceful shutdown complete" message).
    """
    def _handler(signum: int, frame: Any) -> None:  # noqa: ARG001
        log.info("Received signal %d — initiating graceful shutdown", signum)
        if _uvicorn_server is not None:
            _uvicorn_server.should_exit = True
        else:
            # Fallback: raise SystemExit so Python unwinds cleanly
            raise SystemExit(0)

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Start the uvicorn HTTP server on port 4001."""
    global _uvicorn_server

    port = int(os.getenv("ORCHESTRATOR_PORT", "4001"))
    host = os.getenv("ORCHESTRATOR_HOST", "0.0.0.0")
    log_level = os.getenv("ORCHESTRATOR_LOG_LEVEL", "info").lower()

    config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_level=log_level,
        # Give the lifespan shutdown handler up to 35s
        # (30s wait + 5s buffer for cleanup)
        timeout_graceful_shutdown=35,
    )

    _uvicorn_server = uvicorn.Server(config)
    _install_sigterm_handler()

    log.info("Starting uvicorn on %s:%d", host, port)
    _uvicorn_server.run()


if __name__ == "__main__":
    main()
