#!/usr/bin/env python3
"""tac-master dashboard event sender.

Streams Claude Code hook events to the tac-master dashboard server so
every agent phase is visible in real time across every repo.

IMPORTANT: this script uses ONLY stdlib (urllib). It does NOT use uv or
httpx because Claude Code invokes hooks via the shell PATH that its
parent process saw, and `uv` is often not in that PATH when the ADW
subprocess tree fires hooks. A pure python3 shebang + stdlib guarantees
the hook runs in any environment where python3 exists (i.e. everywhere).

Wired into .claude/settings.json for all 7 hook events. Every invocation:
  1. Reads the hook payload JSON from stdin
  2. Enriches with repo_url, adw_id, phase (derived from cwd / env)
  3. Detects block_type and extracts tool_name from payload
  4. POSTs to $TAC_DASHBOARD_URL/events (default http://localhost:4000/events)
  5. Exits 0 no matter what so Claude Code is never blocked

Never raises. Never writes to stderr unless debug mode. Never interrupts the
agent — the dashboard is observability, not control-plane.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib import request as urlrequest
from urllib.error import URLError

DEFAULT_URL = os.getenv("TAC_DASHBOARD_URL", "http://localhost:4000/events")
DEBUG = os.getenv("TAC_HOOK_DEBUG", "").lower() in ("1", "true", "yes")


def _debug(msg: str) -> None:
    if DEBUG:
        print(f"[send_event] {msg}", file=sys.stderr)


def detect_repo_url() -> str | None:
    """Best-effort detection of the current repo URL from git remote."""
    env_url = os.getenv("GITHUB_REPO_URL")
    if env_url:
        return _strip_auth(env_url)
    try:
        out = subprocess.check_output(
            ["git", "remote", "get-url", "origin"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=3,
        ).strip()
        return _normalize_remote(out)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _normalize_remote(url: str) -> str:
    if url.startswith("git@"):
        host, path = url.split(":", 1)
        host = host.replace("git@", "")
        return f"https://{host}/{path.removesuffix('.git')}"
    return _strip_auth(url).removesuffix(".git")


def _strip_auth(url: str) -> str:
    if "@" not in url or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    if "@" in rest:
        rest = rest.split("@", 1)[1]
    return f"{scheme}://{rest}"


def detect_adw_id() -> str | None:
    env_id = os.getenv("ADW_ID")
    if env_id:
        return env_id

    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents][:5]:
        agents = parent / "agents"
        if agents.is_dir():
            candidates = [p for p in agents.iterdir() if p.is_dir() and not p.name.startswith("_")]
            if candidates:
                candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                return candidates[0].name
        if "__" in parent.name:
            suffix = parent.name.rsplit("__", 1)[1]
            if len(suffix) == 8 and all(c in "0123456789abcdef" for c in suffix):
                return suffix
    return None


def detect_phase() -> str | None:
    cmd = os.getenv("CLAUDE_COMMAND") or os.getenv("ADW_PHASE")
    if not cmd:
        return None
    for phase in ("plan", "build", "test", "review", "document", "ship", "reflect", "patch"):
        if phase in cmd.lower():
            return phase
    return cmd


def detect_block_type(event_type: str, payload: dict[str, Any]) -> str:
    """
    Detect the block_type from the hook payload.

    For PreToolUse/PostToolUse events: inspect payload for block content:
      - If payload contains tool_input with 'thinking' key -> 'thinking_block'
      - If payload contains tool_name -> 'tool_use_block'
      - Otherwise -> 'hook_lifecycle' (default)

    For other events -> 'hook_lifecycle' (default)

    Returns one of: 'thinking_block', 'tool_use_block', 'text_block', 'hook_lifecycle'
    """
    try:
        if event_type not in ("PreToolUse", "PostToolUse"):
            return "hook_lifecycle"

        # Check for thinking content in tool_input (some tools are thinking assistants)
        tool_input = payload.get("tool_input")
        if isinstance(tool_input, dict):
            if "thinking" in tool_input or (tool_input.get("type") == "thinking"):
                return "thinking_block"

        # Check for tool_use (if tool_name exists, it's a tool use block)
        if "tool_name" in payload:
            return "tool_use_block"

        # Default for other content types
        return "hook_lifecycle"

    except Exception as e:
        _debug(f"Error detecting block_type: {e}")
        return "hook_lifecycle"


def extract_tool_name(payload: dict[str, Any]) -> str | None:
    """
    Extract tool_name from the hook payload for fast filtering.

    Returns the tool_name if present, otherwise None.
    """
    try:
        if isinstance(payload, dict):
            return payload.get("tool_name")
    except Exception as e:
        _debug(f"Error extracting tool_name: {e}")
    return None


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


def post_json(url: str, payload: dict, timeout: float = 2.0) -> None:
    """Fire-and-forget POST. Swallows all errors."""
    try:
        body = json.dumps(payload).encode("utf-8")
        req = urlrequest.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            resp.read()  # drain to complete the request
    except URLError as e:
        _debug(f"POST {url} failed: {e}")
    except Exception as e:  # noqa: BLE001 — intentional: never let a hook fail
        _debug(f"POST {url} error: {e}")


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

    # Detect block_type and extract tool_name from payload
    block_type = detect_block_type(args.event_type, payload)
    tool_name = extract_tool_name(payload)

    event = {
        "source_app": args.source_app,
        "session_id": session_id,
        "hook_event_type": args.event_type,
        "block_type": block_type,
        "repo_url": detect_repo_url(),
        "adw_id": detect_adw_id(),
        "phase": detect_phase(),
        "payload": payload,
    }

    # Include tool_name as top-level field if present
    if tool_name is not None:
        event["tool_name"] = tool_name

    # Optional chat transcript (last ~40 turns)
    if args.add_chat and "transcript_path" in payload:
        tp = payload.get("transcript_path")
        if tp and isinstance(tp, str) and os.path.exists(tp):
            try:
                with open(tp) as f:
                    lines = [ln for ln in f if ln.strip()]
                event["chat"] = [
                    json.loads(ln) for ln in lines[-40:]
                ]
            except Exception:
                pass

    post_json(args.server_url, event)
    return 0  # always succeed — never block Claude Code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # Triple-belt-and-suspenders: any unexpected error still exits 0
        sys.exit(0)
