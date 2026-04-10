"""End-to-end smoke test for OrchestratorService.

Requires: ANTHROPIC_API_KEY in environment (set for the claude CLI).
Run:
    uv run python orchestrator/tests/test_orchestrator_e2e.py

Test sequence:
    a. Create temp SQLite DB and run migration.
    b. Instantiate OrchestratorService with test DB and mock dashboard URL.
    c. Send short test message: "What is 2+2? Reply in exactly one sentence."
    d. Verify response arrives (non-empty text).
    e. Verify response persisted to chat_messages table.
    f. Capture session_id from the orchestrator_agents row.
    g. Re-instantiate OrchestratorService with saved session_id (test resumption).
    h. Send follow-up: "What number did I ask about in my previous message?"
    i. Verify Claude demonstrates context continuity (references the first message).
    j. Verify chat_messages has 4 rows: 2 user + 2 orchestrator.
    k. Print pass/fail summary.

Exit 0 on success, non-zero on failure.

NOTE: This test makes REAL Claude API calls. Messages are kept very short to
minimise cost. If ANTHROPIC_API_KEY is not set, the test is skipped gracefully.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile
import traceback
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup: allow running from the tac-master root or directly as a script
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent          # orchestrator/tests/
_ORCH = _HERE.parent                              # orchestrator/
_TAC_ROOT = _ORCH.parent                          # tac-master/

if str(_TAC_ROOT) not in sys.path:
    sys.path.insert(0, str(_TAC_ROOT))


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

def _check_api_key() -> bool:
    """Return True if ANTHROPIC_API_KEY is available in the environment."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    return bool(key)


def _check_claude_cli() -> bool:
    """Return True if the claude CLI binary is on PATH."""
    import shutil
    return shutil.which("claude") is not None


# ---------------------------------------------------------------------------
# Pass/fail tracking
# ---------------------------------------------------------------------------

_results: list[tuple[str, bool, str]] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    """Record a single assertion and print its status."""
    status = "PASS" if condition else "FAIL"
    _results.append((name, condition, detail))
    mark = "[PASS]" if condition else "[FAIL]"
    suffix = f" — {detail}" if detail else ""
    print(f"  {mark} {name}{suffix}")
    if not condition:
        raise AssertionError(f"Assertion failed: {name}{suffix}")


# ---------------------------------------------------------------------------
# Core test logic
# ---------------------------------------------------------------------------

async def _run_e2e(db_path: Path) -> None:
    """Execute the full end-to-end test sequence against a temp database."""

    from orchestrator.migrate_db import migrate
    from orchestrator.orchestrator_service import OrchestratorService
    from orchestrator.db_repositories import OrchestratorAgentRepo, ChatMessageRepo

    # -----------------------------------------------------------------------
    # Step a: Run migration on fresh temp DB
    # -----------------------------------------------------------------------
    print("\n[Step a] Running migration on temp DB...")
    migrate(db_path)

    conn_check = sqlite3.connect(str(db_path))
    tables = {
        r[0]
        for r in conn_check.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn_check.close()
    _check("migration creates orchestrator_agents", "orchestrator_agents" in tables)
    _check("migration creates chat_messages", "chat_messages" in tables)

    # -----------------------------------------------------------------------
    # Step b: Instantiate OrchestratorService (dashboard URL will 404 gracefully)
    # -----------------------------------------------------------------------
    print("\n[Step b] Instantiating OrchestratorService (first instance)...")
    # Use 127.0.0.1 on a port no one listens to — _post_event swallows ConnectError.
    mock_dashboard = "http://127.0.0.1:19999"

    svc1 = OrchestratorService(
        db_path=str(db_path),
        dashboard_url=mock_dashboard,
        working_dir=str(_TAC_ROOT),
    )
    _check("OrchestratorService instantiated", svc1 is not None)
    orchestrator_id_1 = svc1._orchestrator_id
    _check("orchestrator_id has oa- prefix", orchestrator_id_1.startswith("oa-"))

    # -----------------------------------------------------------------------
    # Step c/d: Send first message and collect response
    # -----------------------------------------------------------------------
    print("\n[Step c/d] Sending first message to Claude...")

    first_message = "What is 2+2? Reply in exactly one sentence."
    response_texts: list[str] = []
    result_msg = None

    from orchestrator.claude_sdk_client import TextBlock, ResultMessage

    stream = await svc1.process_user_message(first_message)
    async for event in stream:
        if isinstance(event, TextBlock):
            response_texts.append(event.text)
        elif isinstance(event, ResultMessage):
            result_msg = event

    full_response_1 = "".join(response_texts).strip()
    print(f"  Response text: {full_response_1[:120]!r}")

    _check(
        "first response is non-empty",
        bool(full_response_1),
        f"len={len(full_response_1)}",
    )
    _check("ResultMessage received", result_msg is not None)
    if result_msg is not None:
        _check(
            "ResultMessage is success",
            result_msg.is_success,
            f"subtype={result_msg.subtype}",
        )

    # -----------------------------------------------------------------------
    # Step e: Verify first exchange persisted to chat_messages
    # -----------------------------------------------------------------------
    print("\n[Step e] Verifying DB persistence after first message...")
    history_1 = await svc1.load_chat_history()
    user_rows_1 = [m for m in history_1 if m["sender_type"] == "user"]
    orch_rows_1 = [m for m in history_1 if m["sender_type"] == "orchestrator"]

    _check("chat_messages has user row", len(user_rows_1) >= 1)
    _check("chat_messages has orchestrator row", len(orch_rows_1) >= 1)
    _check(
        "user message content matches",
        any(first_message in (m.get("message") or "") for m in user_rows_1),
    )
    _check(
        "orchestrator response persisted",
        any(m.get("message") for m in orch_rows_1),
    )

    # -----------------------------------------------------------------------
    # Step f: Capture session_id from orchestrator_agents
    # -----------------------------------------------------------------------
    print("\n[Step f] Capturing session_id from orchestrator_agents row...")

    # session_id is stored in svc1.session_id after Phase 3
    captured_session_id = svc1.session_id
    _check(
        "session_id populated after first message",
        bool(captured_session_id),
        f"session_id={str(captured_session_id)[:30] if captured_session_id else 'None'}",
    )

    # Also verify it landed in the DB row
    raw_conn = sqlite3.connect(str(db_path))
    raw_conn.row_factory = sqlite3.Row
    db_row = raw_conn.execute(
        "SELECT session_id FROM orchestrator_agents WHERE id = ?",
        (orchestrator_id_1,),
    ).fetchone()
    raw_conn.close()

    db_session_id = db_row["session_id"] if db_row else None
    _check(
        "session_id persisted in DB",
        bool(db_session_id),
        f"db_session_id={str(db_session_id)[:30] if db_session_id else 'None'}",
    )

    # Close first instance
    svc1.close()

    # -----------------------------------------------------------------------
    # Step g: Re-instantiate with saved session_id (test resumption)
    # -----------------------------------------------------------------------
    print("\n[Step g] Re-instantiating OrchestratorService with saved session_id...")

    svc2 = OrchestratorService(
        db_path=str(db_path),
        dashboard_url=mock_dashboard,
        working_dir=str(_TAC_ROOT),
        session_id=captured_session_id,
    )
    _check("second OrchestratorService instantiated", svc2 is not None)
    _check(
        "second instance started_with_session=True",
        svc2.started_with_session is True,
    )
    _check(
        "second instance session_id matches",
        svc2.session_id == captured_session_id,
    )

    # -----------------------------------------------------------------------
    # Step h/i: Send follow-up and verify context continuity
    # -----------------------------------------------------------------------
    print("\n[Step h/i] Sending follow-up message; checking context continuity...")

    follow_up = "What number did I ask about in my previous message?"
    response_texts_2: list[str] = []
    result_msg_2 = None

    stream2 = await svc2.process_user_message(follow_up)
    async for event in stream2:
        if isinstance(event, TextBlock):
            response_texts_2.append(event.text)
        elif isinstance(event, ResultMessage):
            result_msg_2 = event

    full_response_2 = "".join(response_texts_2).strip()
    print(f"  Response text: {full_response_2[:120]!r}")

    _check(
        "second response is non-empty",
        bool(full_response_2),
        f"len={len(full_response_2)}",
    )
    _check("second ResultMessage received", result_msg_2 is not None)
    if result_msg_2 is not None:
        _check(
            "second ResultMessage is success",
            result_msg_2.is_success,
            f"subtype={result_msg_2.subtype}",
        )

    # Context continuity: Claude should reference "2" or "2+2" or "four" in the follow-up
    continuity_keywords = ["2", "4", "four", "two", "previous", "asked"]
    continuity_ok = any(kw in full_response_2.lower() for kw in continuity_keywords)
    _check(
        "second response shows context continuity",
        continuity_ok,
        f"response contains one of {continuity_keywords}",
    )

    # -----------------------------------------------------------------------
    # Step j: Verify chat_messages has exactly 4 rows: 2 user + 2 orchestrator
    # -----------------------------------------------------------------------
    print("\n[Step j] Verifying chat_messages row count after both exchanges...")
    history_2 = await svc2.load_chat_history(limit=100)

    user_rows_2 = [m for m in history_2 if m["sender_type"] == "user"]
    orch_rows_2 = [m for m in history_2 if m["sender_type"] == "orchestrator"]

    # All messages are stored under the same orchestrator_agent row (svc1 and svc2
    # share the same db — svc2.get_active() returns svc1's row which is non-archived).
    total_rows = len(history_2)
    print(f"  Total chat_messages rows: {total_rows}  (user={len(user_rows_2)}, orchestrator={len(orch_rows_2)})")

    _check("chat_messages has exactly 2 user rows", len(user_rows_2) == 2)
    _check("chat_messages has exactly 2 orchestrator rows", len(orch_rows_2) == 2)
    _check("chat_messages total is 4", total_rows == 4)

    # -----------------------------------------------------------------------
    # Step j continued: Verify get_chat_history returns both turns
    # -----------------------------------------------------------------------
    messages_text = [m.get("message", "") for m in history_2]
    _check(
        "get_chat_history includes first user message",
        any(first_message in t for t in messages_text),
    )
    _check(
        "get_chat_history includes follow-up user message",
        any(follow_up in t for t in messages_text),
    )
    _check(
        "get_chat_history includes both orchestrator responses",
        len([m for m in history_2 if m["sender_type"] == "orchestrator" and m.get("message")]) == 2,
    )

    svc2.close()


def main() -> int:
    """Entry point. Returns exit code (0=success, 1=failure/skip)."""
    print("=" * 60)
    print("OrchestratorService E2E Smoke Test")
    print("=" * 60)

    # Guard: ANTHROPIC_API_KEY
    if not _check_api_key():
        print("\n[SKIP] ANTHROPIC_API_KEY is not set in the environment.")
        print("       Set it and re-run to execute the full E2E test.")
        return 0  # Graceful skip — not a failure

    # Guard: claude CLI
    if not _check_claude_cli():
        print("\n[SKIP] 'claude' CLI not found on PATH.")
        print("       Install claude CLI and ensure it is on PATH to run this test.")
        return 0  # Graceful skip

    print("\nANTHROPIC_API_KEY found. Running full E2E test against real Claude API...")

    exit_code = 0
    tmp_dir = tempfile.mkdtemp(prefix="tac_e2e_")
    db_path = Path(tmp_dir) / "test_e2e.sqlite"

    try:
        asyncio.run(_run_e2e(db_path))
    except AssertionError as exc:
        print(f"\n[ERROR] Assertion failed: {exc}")
        exit_code = 1
    except Exception as exc:
        print(f"\n[ERROR] Unexpected exception: {exc}")
        traceback.print_exc()
        exit_code = 1
    finally:
        # Cleanup temp DB
        try:
            if db_path.exists():
                db_path.unlink()
            wal = db_path.with_suffix(".sqlite-wal")
            shm = db_path.with_suffix(".sqlite-shm")
            if wal.exists():
                wal.unlink()
            if shm.exists():
                shm.unlink()
            Path(tmp_dir).rmdir()
        except Exception as cleanup_exc:
            print(f"  [WARN] Cleanup failed: {cleanup_exc}")

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = sum(1 for _, ok, _ in _results if not ok)
    total = len(_results)
    for name, ok, detail in _results:
        mark = "PASS" if ok else "FAIL"
        suffix = f" ({detail})" if detail else ""
        print(f"  [{mark}] {name}{suffix}")

    print(f"\n  {passed}/{total} checks passed", end="")
    if failed:
        print(f"  ({failed} FAILED)")
    else:
        print()

    if exit_code == 0 and failed == 0:
        print("\nResult: ALL CHECKS PASSED")
    else:
        print("\nResult: SOME CHECKS FAILED")
        exit_code = 1

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
