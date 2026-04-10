#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "pyyaml>=6.0",
#   "python-dotenv>=1.0",
#   "httpx>=0.27",
# ]
# ///

"""tac-master orchestrator daemon.

Long-running loop that polls GitHub across allowlisted repos, dispatches
Lead processes for qualifying issues, reaps finished runs, and handles
graceful shutdown.

Usage:
    uv run orchestrator/daemon.py                 # foreground, loop
    uv run orchestrator/daemon.py --once          # single poll cycle (cron mode)
    uv run orchestrator/daemon.py --dry-run       # poll but do not dispatch
    uv run orchestrator/daemon.py --doctor        # validate config + exit

Under systemd:  see deploy/systemd/tac-master.service
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import logging
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Optional

# Make orchestrator package importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator.budget import BudgetEnforcer
from orchestrator.config import TacMasterConfig, load_config
from orchestrator.config_validator import validate_all, has_fatal
from orchestrator.dispatcher import Dispatcher
from orchestrator.github_client import GitHubClient
from orchestrator.knowledge import KnowledgeBase
from orchestrator.repo_manager import RepoManager
from orchestrator.state_store import StateStore
from orchestrator.token_tracker import TokenTracker


_shutdown = False

_orch_log = logging.getLogger("daemon.orchestrator")


# ---------------------------------------------------------------------------
# _OrchestratorBridge — thin synchronous wrapper around the async
# OrchestratorService.  Runs its own asyncio event loop on a daemon thread so
# the synchronous daemon poll loop can call it without blocking.
# ---------------------------------------------------------------------------

class _OrchestratorBridge:
    """Runs OrchestratorService on a background asyncio thread.

    The daemon is synchronous; OrchestratorService is async.  This bridge
    owns a dedicated asyncio event loop running on a daemon thread and exposes
    two synchronous entry points used by daemon.py:

    * ``consult_issue(issue_title, issue_body, repo_url)``
        Fire-and-forget: sends an RCASD analysis message to the orchestrator
        and returns immediately.  The orchestrator thinks in the background
        and its conclusions are persisted to SQLite / POSTed to the dashboard.
        Returns True if the message was submitted, False if the bridge is busy
        or not initialised.

    * ``shutdown()``
        Persists session state and stops the background thread cleanly.

    Parameters
    ----------
    db_path:
        Path to the shared tac_master.sqlite database.
    state_store:
        StateStore instance shared with the daemon and dispatcher.
    working_dir:
        CWD for the claude subprocess (tac-master root).
    dashboard_url:
        Base URL of the dashboard server (default http://localhost:4000).
    model:
        Claude model alias (default ``"sonnet"``).
    """

    _RCASD_TEMPLATE = (
        "New GitHub issue requires RCASD analysis and dispatch decision.\n\n"
        "**Repository**: {repo_url}\n"
        "**Issue title**: {issue_title}\n"
        "**Issue body**:\n{issue_body}\n\n"
        "Please:\n"
        "1. Perform Root Cause Analysis / Situation Diagnosis (RCASD) on this issue.\n"
        "2. Decide which workflow (patch / plan_build / plan_build_test / sdlc) best fits.\n"
        "3. Identify any risks or blockers the ADW should be aware of.\n"
        "4. Summarise your analysis so the execution engine can proceed.\n"
    )

    def __init__(
        self,
        db_path: str,
        state_store: StateStore,
        working_dir: str,
        dashboard_url: str = "http://localhost:4000",
        model: str = "sonnet",
    ) -> None:
        self._db_path = db_path
        self._state_store = state_store
        self._working_dir = working_dir
        self._dashboard_url = dashboard_url
        self._model = model

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._service: Optional[object] = None  # OrchestratorService
        self._ready = threading.Event()
        self._init_error: Optional[Exception] = None

    # ------------------------------------------------------------------
    # Public API

    def start(self) -> bool:
        """Start the background thread and initialise OrchestratorService.

        Returns True if the bridge started successfully, False on error.
        The daemon should continue without the bridge on False.
        """
        self._thread = threading.Thread(
            target=self._run_loop,
            name="orchestrator-bridge",
            daemon=True,
        )
        self._thread.start()
        # Wait up to 30 s for the service to initialise
        if not self._ready.wait(timeout=30):
            _orch_log.warning(
                "OrchestratorBridge did not become ready within 30 s — "
                "proceeding without orchestrator."
            )
            return False
        if self._init_error is not None:
            _orch_log.warning(
                "OrchestratorBridge init failed: %s — proceeding without orchestrator.",
                self._init_error,
            )
            return False
        _orch_log.info("OrchestratorBridge ready (model=%s)", self._model)
        return True

    def consult_issue(
        self,
        issue_title: str,
        issue_body: str,
        repo_url: str,
    ) -> bool:
        """Submit an RCASD analysis request to the orchestrator (non-blocking).

        The orchestrator thinks about the issue asynchronously.  The daemon
        does NOT wait for the analysis before proceeding with dispatch — the
        analysis is advisory, persisted to SQLite and the dashboard.

        Returns True if the message was queued, False if unavailable.
        """
        if self._loop is None or self._service is None:
            return False
        if not self._loop.is_running():
            return False

        message = self._RCASD_TEMPLATE.format(
            repo_url=repo_url,
            issue_title=issue_title,
            issue_body=(issue_body or "(no body)").strip()[:2000],
        )

        async def _send() -> None:
            try:
                svc = self._service
                if svc is None:
                    return
                # process_user_message returns an async generator; consume it
                gen = await svc.process_user_message(message)
                async for _ in gen:
                    pass
            except Exception as exc:
                _orch_log.warning("RCASD analysis error for %s: %s", repo_url, exc)

        asyncio.run_coroutine_threadsafe(_send(), self._loop)
        _orch_log.info(
            "OrchestratorBridge: queued RCASD analysis for %s — %s",
            repo_url, issue_title[:80],
        )
        return True

    def shutdown(self) -> None:
        """Gracefully stop the bridge; persist session state."""
        if self._loop is None or not self._loop.is_running():
            return

        async def _stop() -> None:
            svc = self._service
            if svc is not None:
                try:
                    svc.close()
                    _orch_log.info("OrchestratorService closed; session state preserved.")
                except Exception as exc:
                    _orch_log.warning("OrchestratorService close error: %s", exc)
            self._loop.stop()  # type: ignore[union-attr]

        asyncio.run_coroutine_threadsafe(_stop(), self._loop)
        if self._thread is not None:
            self._thread.join(timeout=10)

    # ------------------------------------------------------------------
    # Internal

    def _run_loop(self) -> None:
        """Entry point for the background daemon thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._init_service())
            self._ready.set()
            if self._init_error is None:
                self._loop.run_forever()
        except Exception as exc:
            self._init_error = exc
            self._ready.set()
        finally:
            try:
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            except Exception:
                pass
            self._loop.close()

    async def _init_service(self) -> None:
        """Initialise OrchestratorService inside the event loop thread."""
        try:
            from orchestrator.orchestrator_service import OrchestratorService

            # Attempt to recover the existing session_id from SQLite so the
            # orchestrator can resume its Claude SDK conversation across daemon
            # restarts.  OrchestratorService.__init__ handles get_active()
            # internally; we only need to pass the existing session_id if one
            # was previously persisted to orchestrator_agents.session_id.
            existing_session_id = self._resolve_existing_session_id()

            self._service = OrchestratorService(
                db_path=self._db_path,
                dashboard_url=self._dashboard_url,
                working_dir=self._working_dir,
                session_id=existing_session_id,
                state_store=self._state_store,
                model=self._model,
            )
        except Exception as exc:
            self._init_error = exc

    def _resolve_existing_session_id(self) -> Optional[str]:
        """Look up the session_id from the active orchestrator_agents row.

        Opens a short-lived read-only connection to avoid touching the shared
        StateStore connection (which is not thread-safe).  Returns None if no
        active row exists or none has a session_id.
        This enables session resumption across daemon restarts.
        """
        import sqlite3 as _sqlite3

        try:
            from orchestrator.db_repositories import OrchestratorAgentRepo

            # Open a dedicated connection to avoid thread-safety issues with
            # the shared StateStore._conn.
            conn = _sqlite3.connect(self._db_path)
            conn.row_factory = _sqlite3.Row
            try:
                row = OrchestratorAgentRepo.get_active(conn)
            finally:
                conn.close()

            if row is not None:
                sid = row.get("session_id")
                if sid:
                    _orch_log.info(
                        "OrchestratorBridge: found existing session_id %s… — will resume",
                        str(sid)[:20],
                    )
                    return str(sid)
        except Exception as exc:
            _orch_log.debug("Could not resolve existing session_id: %s", exc)
        return None


def _build_orchestrator_bridge(
    cfg: TacMasterConfig,
    store: StateStore,
) -> Optional[_OrchestratorBridge]:
    """Construct and start an _OrchestratorBridge.

    Returns the bridge on success, or None if the bridge could not be started
    (import error, init failure, etc.).  The caller continues normally when
    None is returned — all existing dispatch behaviour is preserved.
    """
    try:
        db_path = str(cfg.sqlite_path)
        working_dir = str(cfg.home)
        # Strip /events suffix if the operator set TAC_DASHBOARD_URL to the
        # full events endpoint rather than the base URL.
        dashboard_url = (
            cfg.identity.get("TAC_DASHBOARD_URL", "http://localhost:4000")
            .replace("/events", "")
        )
        model = cfg.identity.get("TAC_ORCHESTRATOR_MODEL", "sonnet")

        bridge = _OrchestratorBridge(
            db_path=db_path,
            state_store=store,
            working_dir=working_dir,
            dashboard_url=dashboard_url,
            model=model,
        )
        ok = bridge.start()
        return bridge if ok else None
    except Exception as exc:
        logging.getLogger("daemon").warning(
            "OrchestratorBridge unavailable: %s — falling back to label-only dispatch.",
            exc,
        )
        return None


def _install_signal_handlers() -> None:
    def handler(signum, frame):
        global _shutdown
        logging.info("Received signal %d, shutting down after current cycle", signum)
        _shutdown = True

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


def _configure_logging(cfg: TacMasterConfig) -> None:
    level_name = cfg.identity.get("TAC_MASTER_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    log_file = cfg.logs_dir / "daemon.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file),
    ]
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
    )


def _build_system(cfg: TacMasterConfig) -> tuple[Dispatcher, StateStore, GitHubClient]:
    store = StateStore(cfg.sqlite_path)
    # Pre-create KB schema so FTS5 triggers exist before any reflect runs
    KnowledgeBase(store)
    gh = GitHubClient(cfg.identity["GITHUB_PAT"])
    repo_mgr = RepoManager(cfg.home, cfg.repos_dir, cfg.trees_dir, cfg.identity)
    budget = BudgetEnforcer(cfg.budgets, store)
    tokens = TokenTracker(store, cfg.home / "config" / "model_prices.yaml")
    dispatcher = Dispatcher(cfg, store, repo_mgr, gh, budget, tokens)
    return dispatcher, store, gh


def _wire_orchestrator(
    dispatcher: Dispatcher,
    bridge: _OrchestratorBridge,
    log: logging.Logger,
) -> None:
    """Monkey-patch dispatcher._dispatch to consult the orchestrator first.

    The OrchestratorService is the brain; the Dispatcher is the execution
    engine.  For each new issue that qualifies for dispatch, we fire an
    asynchronous RCASD analysis via the bridge before the ADW is launched.
    The analysis is non-blocking and advisory — dispatch always proceeds
    regardless of whether the orchestrator is available or busy.

    This patch preserves all existing dispatch logic (budget, label matching,
    workflow selection) and simply adds the orchestrator consultation as a
    side-effect.
    """
    original_dispatch = dispatcher._dispatch  # type: ignore[attr-defined]

    def _dispatch_with_orchestrator(repo, issue):
        # Fire RCASD analysis (non-blocking, fire-and-forget)
        try:
            bridge.consult_issue(
                issue_title=issue.title,
                issue_body=issue.body or "",
                repo_url=repo.url,
            )
        except Exception as exc:
            log.debug(
                "OrchestratorBridge.consult_issue raised unexpectedly "
                "(non-blocking): %s",
                exc,
            )
        # Delegate to original dispatch logic regardless
        return original_dispatch(repo, issue)

    dispatcher._dispatch = _dispatch_with_orchestrator  # type: ignore[attr-defined]
    log.info(
        "OrchestratorService wired to dispatcher — "
        "new issues will be routed through RCASD analysis."
    )


# ----------------------------------------------------------------------------
def cmd_doctor(cfg: TacMasterConfig) -> int:
    """Validate config and environment. Exit code = number of problems."""
    print(f"doctor UTC:    {datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')}")
    problems = 0
    print(f"home:          {cfg.home}")
    print(f"sqlite:        {cfg.sqlite_path}")
    print(f"repos:         {len(cfg.repos.repos)} allowlisted")
    for r in cfg.repos.repos:
        flag = " [self]" if r.self else ""
        print(f"  • {r.url}  → {r.default_workflow} ({r.model_set}){flag}")

    # Check identity
    for key in ("GITHUB_USER", "GITHUB_PAT", "ANTHROPIC_API_KEY"):
        present = bool(cfg.identity.get(key))
        print(f"  {key}: {'✓ set' if present else '✗ MISSING'}")
        if not present:
            problems += 1

    # Check GitHub connectivity
    try:
        gh = GitHubClient(cfg.identity.get("GITHUB_PAT", ""))
        r = gh.client.get("/user")
        if r.status_code == 200:
            user = r.json().get("login")
            print(f"  github auth: ✓ as {user}")
        else:
            print(f"  github auth: ✗ status {r.status_code}")
            problems += 1
        gh.close()
    except Exception as e:
        print(f"  github auth: ✗ {e}")
        problems += 1

    # Check substrate symlinks present
    for sub in ("adws", ".claude"):
        p = cfg.home / sub
        print(f"  substrate {sub}: {'✓' if p.exists() else '✗ MISSING'}")
        if not p.exists():
            problems += 1

    # Check MCP configs for the review phase
    for mcp in (".mcp.json", "playwright-mcp-config.json"):
        p = cfg.home / mcp
        print(f"  mcp config {mcp}: {'✓' if p.exists() else '⚠ missing (review phase degraded)'}")

    # Check Claude Code CLI is installed and callable
    import shutil as _sh
    import subprocess as _sp
    claude_bin = cfg.identity.get("CLAUDE_CODE_PATH") or _sh.which("claude")
    if not claude_bin:
        print("  claude code: ✗ NOT FOUND in PATH")
        problems += 1
    else:
        try:
            r = _sp.run([claude_bin, "--version"], capture_output=True,
                        text=True, timeout=10)
            if r.returncode == 0:
                print(f"  claude code: ✓ {r.stdout.strip()} ({claude_bin})")
            else:
                print(f"  claude code: ✗ --version exited {r.returncode}: {r.stderr.strip()}")
                problems += 1
        except (FileNotFoundError, _sp.TimeoutExpired) as e:
            print(f"  claude code: ✗ {e}")
            problems += 1

    # Check Playwright Chromium cache exists (for review phase)
    import os as _os
    home_dir = _os.path.expanduser("~")
    pw_cache = Path(home_dir) / ".cache" / "ms-playwright"
    if pw_cache.exists():
        print(f"  playwright: ✓ cache at {pw_cache}")
    else:
        print(f"  playwright: ⚠ no cache at {pw_cache} (review phase will fail)")

    # Podman is only required if a repo opted into it
    podman_repos = [r for r in cfg.repos.repos if r.runtime == "podman"]
    if podman_repos:
        print(f"  runtime: {len(podman_repos)} repo(s) use podman — verifying...")
        podman_bin = _sh.which("podman")
        if not podman_bin:
            print(f"  podman: ✗ NOT FOUND but required by: "
                  f"{', '.join(r.slug for r in podman_repos)}")
            problems += 1
        else:
            try:
                r = _sp.run([podman_bin, "--version"], capture_output=True,
                            text=True, timeout=10)
                print(f"  podman: ✓ {r.stdout.strip()}")
                # Check that each distinct image is available
                seen_images = set()
                for repo in podman_repos:
                    img = repo.container_image
                    if img in seen_images:
                        continue
                    seen_images.add(img)
                    probe = _sp.run(
                        [podman_bin, "image", "exists", img],
                        capture_output=True,
                    )
                    if probe.returncode == 0:
                        print(f"  image {img}: ✓")
                    else:
                        print(f"  image {img}: ✗ not built — run "
                              f"'bash deploy/docker/build.sh' or "
                              f"'sudo bash scripts/tac-update.sh install-podman'")
                        problems += 1
            except Exception as e:
                print(f"  podman: ✗ {e}")
                problems += 1
    else:
        print("  runtime: all repos use native (podman not required)")

    # Check state store + knowledge base + token tracker schemas
    # Each subsystem's __init__ lazily creates its tables via CREATE TABLE IF NOT EXISTS
    try:
        store = StateStore(cfg.sqlite_path)
        KnowledgeBase(store)  # creates lessons + FTS5 tables
        TokenTracker(store, cfg.home / "config" / "model_prices.yaml")  # creates token_ledger
        with store.conn() as c:
            tables = [r["name"] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()]
        required = {"repos", "issues", "runs", "phases", "events",
                    "budget_usage", "token_ledger", "lessons"}
        missing = required - set(tables)
        if missing:
            print(f"  schema: ✗ missing {missing}")
            problems += 1
        else:
            print(f"  schema: ✓ {len(tables)} tables (incl. FTS5)")
        store.close()
    except Exception as e:
        print(f"  schema: ✗ {e}")
        problems += 1

    # Check price book
    prices = cfg.home / "config" / "model_prices.yaml"
    print(f"  pricing: {'✓' if prices.exists() else '✗ MISSING'}")
    if not prices.exists():
        problems += 1

    # Check dashboard server presence (optional, warn-only)
    dash = cfg.home / "dashboard" / "server" / "src" / "index.ts"
    print(f"  dashboard: {'✓' if dash.exists() else '⚠ not built (optional)'}")

    print(f"\n{'✓ all checks passed' if problems == 0 else f'✗ {problems} problem(s)'}")
    return problems


# ----------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(prog="tac-master-daemon")
    ap.add_argument("--once", action="store_true",
                    help="Run a single poll cycle and exit (cron mode)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Poll and log decisions but do not dispatch")
    ap.add_argument("--doctor", action="store_true",
                    help="Validate config and environment, then exit")
    ap.add_argument("--home", type=Path, default=None,
                    help="Override TAC_MASTER_HOME")
    ap.add_argument("--no-orchestrator", action="store_true",
                    help="Disable OrchestratorService (fall back to label-only dispatch)")
    args = ap.parse_args()

    try:
        cfg = load_config(args.home)
    except Exception as e:
        print(f"CONFIG ERROR: {e}", file=sys.stderr)
        return 2

    _configure_logging(cfg)
    log = logging.getLogger("daemon")

    # Validate user config files for structural issues and placeholder values.
    # Warnings are emitted to the log; FATAL warnings abort startup.
    _config_warnings = validate_all(cfg.home / "config")
    for _w in _config_warnings:
        if _w.startswith("FATAL:"):
            log.error("Config validation %s", _w)
        else:
            log.warning("Config validation %s", _w)
    if has_fatal(_config_warnings):
        log.error(
            "Startup aborted: fix the FATAL config issues above, then restart the daemon."
        )
        return 3

    if args.doctor:
        return cmd_doctor(cfg)

    dispatcher, store, gh = _build_system(cfg)

    # ── OrchestratorService integration ───────────────────────────────────
    # Start the OrchestratorService alongside the daemon as a long-lived
    # process.  If startup fails or --no-orchestrator is set, the daemon
    # falls back to the existing label-only dispatch behaviour unchanged.
    bridge: Optional[_OrchestratorBridge] = None
    if not args.dry_run and not args.no_orchestrator:
        bridge = _build_orchestrator_bridge(cfg, store)
        if bridge is not None:
            _wire_orchestrator(dispatcher, bridge, log)
        else:
            log.info(
                "OrchestratorService not available — using label-only dispatch."
            )
    # ─────────────────────────────────────────────────────────────────────

    if args.dry_run:
        # Patch _dispatch to a no-op for dry-run
        _original = dispatcher._dispatch
        def dry(_repo, _issue):
            log.info("[dry-run] would dispatch %s#%d", _repo.url, _issue.number)
            return False
        dispatcher._dispatch = dry  # type: ignore

    _install_signal_handlers()
    poll_interval = int(cfg.identity.get("TAC_MASTER_POLL_INTERVAL", "20"))
    log.info(
        "tac-master daemon starting (poll=%ds, repos=%d, orchestrator=%s)",
        poll_interval,
        len(cfg.repos.repos),
        "enabled" if bridge is not None else "disabled",
    )

    try:
        if args.once:
            dispatched = dispatcher.poll_once()
            dispatcher.reap_finished_runs()
            log.info("Single cycle complete. dispatched=%d", dispatched)
            return 0

        # Main loop
        while not _shutdown:
            try:
                dispatched = dispatcher.poll_once()
                dispatcher.reap_finished_runs()
                if dispatched:
                    log.info("Cycle dispatched=%d", dispatched)
            except Exception as e:
                log.exception("Cycle error: %s", e)

            # Sleep with shutdown check
            for _ in range(poll_interval):
                if _shutdown:
                    break
                time.sleep(1)

        log.info("Daemon shutting down gracefully")
        return 0
    finally:
        # Graceful shutdown: preserve OrchestratorService session state
        # before closing the stores.  This must happen before store.close()
        # so any final DB writes from the bridge can commit successfully.
        if bridge is not None:
            try:
                bridge.shutdown()
            except Exception as exc:
                log.warning("OrchestratorBridge shutdown error: %s", exc)
        gh.close()
        store.close()


if __name__ == "__main__":
    sys.exit(main())
