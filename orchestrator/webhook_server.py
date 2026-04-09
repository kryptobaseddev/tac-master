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

"""tac-master webhook server.

Complements the polling daemon with real-time GitHub webhook ingestion.
Receives push events from multiple allowlisted repos, verifies the HMAC
signature, and routes qualifying issue/comment events through the same
dispatcher the daemon uses.

Why a separate process from the daemon?
  * webhooks are request/response; the daemon loops
  * systemd restarts them independently
  * both share the same SQLite store (WAL mode), config, and dispatcher

Supported events:
  * issues.opened              → dispatch if repo has new_issue trigger
  * issues.labeled             → dispatch if label matches trigger_labels
  * issue_comment.created      → dispatch if body is exactly "adw" or
                                 contains "adw_<workflow>"

Usage:
    uv run orchestrator/webhook_server.py           # foreground
    uv run orchestrator/webhook_server.py --port 8088
    systemctl start tac-master-webhook              # production

All endpoints return 200 quickly to avoid GitHub's 10s delivery timeout.
Heavy lifting (clone, worktree create, spawn) happens in a background
task so the webhook handler returns before dispatch completes.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import sys
from pathlib import Path
from typing import Any

# Make package importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from orchestrator.budget import BudgetEnforcer
from orchestrator.config import TacMasterConfig, load_config
from orchestrator.dispatcher import Dispatcher
from orchestrator.github_client import GitHubClient, Issue
from orchestrator.repo_manager import RepoManager
from orchestrator.state_store import StateStore
from orchestrator.token_tracker import TokenTracker


log = logging.getLogger("webhook")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(cfg: TacMasterConfig) -> FastAPI:
    store = StateStore(cfg.sqlite_path)
    gh = GitHubClient(cfg.identity["GITHUB_PAT"])
    repo_mgr = RepoManager(cfg.home, cfg.repos_dir, cfg.trees_dir, cfg.identity)
    budget = BudgetEnforcer(cfg.budgets, store)
    tokens = TokenTracker(store, cfg.home / "config" / "model_prices.yaml")
    dispatcher = Dispatcher(cfg, store, repo_mgr, gh, budget, tokens)

    app = FastAPI(
        title="tac-master webhook",
        version="0.1",
        description="Multi-repo GitHub webhook ingestion for tac-master",
    )
    app.state.cfg = cfg
    app.state.store = store
    app.state.gh = gh
    app.state.dispatcher = dispatcher

    @app.get("/health")
    async def health() -> dict[str, Any]:
        active = store.active_runs_count()
        return {
            "status": "ok",
            "service": "tac-master-webhook",
            "allowlisted_repos": len(cfg.repos.repos),
            "active_runs": active,
        }

    @app.post("/webhook/github")
    async def github_webhook(
        request: Request,
        background: BackgroundTasks,
        x_github_event: str = Header(default=""),
        x_github_delivery: str = Header(default=""),
        x_hub_signature_256: str = Header(default=""),
    ) -> JSONResponse:
        raw = await request.body()

        # --- signature verification ---
        secret = cfg.identity.get("GITHUB_WEBHOOK_SECRET", "")
        if secret:
            if not _verify_signature(raw, secret, x_hub_signature_256):
                log.warning("Invalid webhook signature (delivery=%s)", x_github_delivery)
                raise HTTPException(status_code=401, detail="invalid signature")
        else:
            log.warning("No webhook secret configured — skipping signature verify")

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="invalid json")

        log.info("webhook event=%s delivery=%s action=%s",
                 x_github_event, x_github_delivery, payload.get("action"))

        decision = _should_dispatch(x_github_event, payload, cfg)
        if not decision.matched:
            return JSONResponse(
                {"status": "ignored", "reason": decision.reason,
                 "delivery": x_github_delivery},
                status_code=202,
            )

        # Return 202 immediately; dispatch in background to beat GH's 10s timeout.
        background.add_task(
            _dispatch_from_webhook,
            dispatcher,
            decision.repo_url,
            decision.issue_number,
            decision.issue_title,
            decision.reason,
        )
        return JSONResponse(
            {
                "status": "accepted",
                "repo": decision.repo_url,
                "issue": decision.issue_number,
                "reason": decision.reason,
                "delivery": x_github_delivery,
            },
            status_code=202,
        )

    return app


# ---------------------------------------------------------------------------
# Webhook routing
# ---------------------------------------------------------------------------


class Decision:
    def __init__(self, matched: bool, reason: str = "",
                 repo_url: str = "", issue_number: int = 0,
                 issue_title: str = ""):
        self.matched = matched
        self.reason = reason
        self.repo_url = repo_url
        self.issue_number = issue_number
        self.issue_title = issue_title


def _should_dispatch(event: str, payload: dict, cfg: TacMasterConfig) -> Decision:
    """Match a GitHub webhook payload against the allowlist and triggers."""
    repo_url_html = (payload.get("repository") or {}).get("html_url", "")
    if not repo_url_html:
        return Decision(False, "no repository in payload")

    repo = cfg.repos.find(repo_url_html)
    if not repo:
        return Decision(False, f"repo not in allowlist: {repo_url_html}")

    action = payload.get("action", "")
    issue = payload.get("issue") or {}
    issue_number = issue.get("number")
    issue_title = issue.get("title", "")

    if not issue_number:
        return Decision(False, "no issue in payload")

    # New issue opened
    if event == "issues" and action == "opened":
        if "new_issue" in repo.triggers:
            return Decision(True, "new_issue", repo.url, issue_number, issue_title)

    # Label added
    if event == "issues" and action == "labeled":
        if "label" in repo.triggers:
            label_name = ((payload.get("label") or {}).get("name", ""))
            if label_name in repo.trigger_labels:
                return Decision(True, f"label:{label_name}",
                                repo.url, issue_number, issue_title)

    # Comment added
    if event == "issue_comment" and action == "created":
        if "comment_adw" in repo.triggers:
            comment = payload.get("comment") or {}
            body = (comment.get("body") or "").strip().lower()
            if body == "adw" or body.startswith("adw_"):
                return Decision(True, f"comment:{body[:20]}",
                                repo.url, issue_number, issue_title)

    return Decision(False, f"no matching trigger for {event}:{action}")


def _dispatch_from_webhook(
    dispatcher: Dispatcher,
    repo_url: str,
    issue_number: int,
    issue_title: str,
    reason: str,
) -> None:
    """Background task: look up repo config, build Issue object, call
    dispatcher._dispatch directly without re-polling GitHub."""
    try:
        repo = dispatcher.cfg.repos.find(repo_url)
        if not repo:
            log.error("Repo vanished between webhook and dispatch: %s", repo_url)
            return
        fake_issue = Issue(
            number=issue_number,
            title=issue_title,
            body="",
            state="open",
            labels=[],
            comments_count=0,
            updated_at="",
            html_url=f"{repo_url}/issues/{issue_number}",
        )
        # seen_issue is needed for status tracking
        dispatcher.store.seen_issue(repo_url, issue_number, issue_title, None)
        ok = dispatcher._dispatch(repo, fake_issue)
        log.info("Background dispatch %s#%d → %s (reason=%s)",
                 repo_url, issue_number, "ok" if ok else "failed", reason)
    except Exception:
        log.exception("Background dispatch crashed for %s#%d",
                      repo_url, issue_number)


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


def _verify_signature(body: bytes, secret: str, sig_header: str) -> bool:
    if not sig_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, sig_header)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(prog="tac-master-webhook")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8088)
    ap.add_argument("--home", type=Path, default=None)
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    try:
        cfg = load_config(args.home)
    except Exception as e:
        print(f"CONFIG ERROR: {e}", file=sys.stderr)
        return 2

    app = create_app(cfg)

    import uvicorn
    log.info("tac-master webhook listening on %s:%d (repos=%d)",
             args.host, args.port, len(cfg.repos.repos))
    uvicorn.run(app, host=args.host, port=args.port, log_config=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
