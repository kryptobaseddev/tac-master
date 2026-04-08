#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.27"]
# ///

"""tac-master dashboard event sender.

Streams Claude Code hook events to the tac-master dashboard server so
every agent phase is visible in real time across every repo.

Wired into .claude/settings.json for all 7 hook events. Every invocation:
  1. Reads the hook payload JSON from stdin
  2. Enriches with repo_url, adw_id, phase (derived from cwd / env)
  3. POSTs to $TAC_DASHBOARD_URL/events (default http://localhost:4000/events)
  4. Exits 0 no matter what so Claude Code is never blocked

Never raises. Never writes to stderr unless debug mode. Never interrupts the
agent — the dashboard is observability, not control-plane.

Usage (from settings.json hooks):
    uv run $CLAUDE_PROJECT_DIR/.claude/hooks/send_event.py \
        --source-app tac-master --event-type PreToolUse
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    import httpx
except ImportError:
    sys.exit(0)  # dependency missing — fail silent


DEFAULT_URL = os.getenv("TAC_DASHBOARD_URL", "http://localhost:4000/events")
DEBUG = os.getenv("TAC_HOOK_DEBUG", "").lower() in ("1", "true", "yes")


def _debug(msg: str) -> None:
    if DEBUG:
        print(f"[send_event] {msg}", file=sys.stderr)


def detect_repo_url() -> str | None:
    """Best-effort detection of the current repo URL from git remote.

    Works inside a worktree because git remote -v is worktree-aware.
    Returns the https URL with any embedded PAT stripped.
    """
    env_url = os.getenv("GITHUB_REPO_URL")
    if env_url:
        return _strip_auth(env_url)
    try:
        out = subprocess.check_output(
            ["git", "remote", "get-url", "origin"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        return _normalize_remote(out)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _normalize_remote(url: str) -> str:
    # git@github.com:owner/repo.git  →  https://github.com/owner/repo
    if url.startswith("git@"):
        host, path = url.split(":", 1)
        host = host.replace("git@", "")
        return f"https://{host}/{path.removesuffix('.git')}"
    return _strip_auth(url).removesuffix(".git")


def _strip_auth(url: str) -> str:
    # https://user:pat@github.com/owner/repo → https://github.com/owner/repo
    if "@" not in url or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    if "@" in rest:
        rest = rest.split("@", 1)[1]
    return f"{scheme}://{rest}"


def detect_adw_id() -> str | None:
    """Find the ADW ID by walking up from cwd looking for agents/<id>/adw_state.json."""
    env_id = os.getenv("ADW_ID")
    if env_id:
        return env_id

    cwd = Path.cwd()
    # tac-7 convention: worktree at trees/<adw_id>/ with agents/<adw_id>/ inside
    for parent in [cwd, *cwd.parents][:5]:
        agents = parent / "agents"
        if agents.is_dir():
            # Pick the most recently modified id
            candidates = [p for p in agents.iterdir() if p.is_dir() and not p.name.startswith("_")]
            if candidates:
                candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                return candidates[0].name
        # Worktree dirs named like {slug}__{adw_id} — extract suffix
        if "__" in parent.name:
            suffix = parent.name.rsplit("__", 1)[1]
            if len(suffix) == 8 and all(c in "0123456789abcdef" for c in suffix):
                return suffix
    return None


def detect_phase() -> str | None:
    """Try to infer the phase from the CLAUDE_COMMAND env var (set by Claude Code
    when running a slash command) or fall back to None."""
    cmd = os.getenv("CLAUDE_COMMAND") or os.getenv("ADW_PHASE")
    if not cmd:
        return None
    for phase in ("plan", "build", "test", "review", "document", "ship", "reflect", "patch"):
        if phase in cmd.lower():
            return phase
    return cmd


def read_stdin_payload() -> dict[str, Any]:
    if sys.stdin.isatty():
        return {}
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return {}
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-app", default="tac-master")
    ap.add_argument("--event-type", required=True)
    ap.add_argument("--server-url", default=DEFAULT_URL)
    ap.add_argument("--add-chat", action="store_true")
    args, _unknown = ap.parse_known_args()

    payload = read_stdin_payload()
    session_id = (
        payload.get("session_id")
        or os.getenv("CLAUDE_SESSION_ID")
        or os.getenv("ADW_ID")
        or "unknown"
    )

    event = {
        "source_app": args.source_app,
        "session_id": session_id,
        "hook_event_type": args.event_type,
        "repo_url": detect_repo_url(),
        "adw_id": detect_adw_id(),
        "phase": detect_phase(),
        "payload": payload,
    }

    # Optional chat transcript
    if args.add_chat and "transcript_path" in payload:
        tp = payload.get("transcript_path")
        if tp and isinstance(tp, str) and os.path.exists(tp):
            try:
                with open(tp) as f:
                    event["chat"] = [
                        json.loads(line) for line in f if line.strip()
                    ][-40:]  # last 40 turns
            except Exception:
                pass

    try:
        with httpx.Client(timeout=2.0) as client:
            client.post(args.server_url, json=event)
    except Exception as e:
        _debug(f"post failed: {e}")

    return 0  # always succeed — never block Claude Code


if __name__ == "__main__":
    sys.exit(main())
