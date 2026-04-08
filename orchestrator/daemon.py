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
import logging
import signal
import sys
import time
from pathlib import Path

# Make orchestrator package importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator.budget import BudgetEnforcer
from orchestrator.config import TacMasterConfig, load_config
from orchestrator.dispatcher import Dispatcher
from orchestrator.github_client import GitHubClient
from orchestrator.repo_manager import RepoManager
from orchestrator.state_store import StateStore


_shutdown = False


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
    gh = GitHubClient(cfg.identity["GITHUB_PAT"])
    repo_mgr = RepoManager(cfg.home, cfg.repos_dir, cfg.trees_dir, cfg.identity)
    budget = BudgetEnforcer(cfg.budgets, store)
    dispatcher = Dispatcher(cfg, store, repo_mgr, gh, budget)
    return dispatcher, store, gh


# ----------------------------------------------------------------------------
def cmd_doctor(cfg: TacMasterConfig) -> int:
    """Validate config and environment. Exit code = number of problems."""
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
    args = ap.parse_args()

    try:
        cfg = load_config(args.home)
    except Exception as e:
        print(f"CONFIG ERROR: {e}", file=sys.stderr)
        return 2

    _configure_logging(cfg)
    log = logging.getLogger("daemon")

    if args.doctor:
        return cmd_doctor(cfg)

    dispatcher, store, gh = _build_system(cfg)

    if args.dry_run:
        # Patch _dispatch to a no-op for dry-run
        original = dispatcher._dispatch
        def dry(_repo, _issue):
            log.info("[dry-run] would dispatch %s#%d", _repo.url, _issue.number)
            return False
        dispatcher._dispatch = dry  # type: ignore

    _install_signal_handlers()
    poll_interval = int(cfg.identity.get("TAC_MASTER_POLL_INTERVAL", "20"))
    log.info("tac-master daemon starting (poll=%ds, repos=%d)",
             poll_interval, len(cfg.repos.repos))

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
        gh.close()
        store.close()


if __name__ == "__main__":
    sys.exit(main())
