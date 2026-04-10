"""Tests for orchestrator.claude_sdk_client.

Covers:
- ClaudeSDKClient.create_session() DB registration
- ClaudeSDKClient.resume_session() with and without existing row
- ClaudeSDKClient.send_message() with mocked subprocess:
    * Yields TextBlock, ThinkingBlock, ToolUseBlock in stream order
    * Yields ResultMessage as final event with usage/cost
    * Persists session_id from system-init line
    * Accumulates tokens in the DB after the stream
- OrchestratorAgentRepo CRUD helpers
- Error path: non-zero exit code is logged but does not raise
"""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.claude_sdk_client import (
    ClaudeSDKClient,
    OrchestratorAgentRepo,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    _parse_content_blocks,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state_store(tmp_path: Path):
    """Create a real StateStore (with V2 schema) backed by a temp file."""
    from orchestrator.state_store import StateStore

    db_path = tmp_path / "test_state.db"
    store = StateStore(db_path)
    # Apply V2 schema so orchestrator_agents table exists
    with store.conn() as c:
        from orchestrator.state_store import SCHEMA_V2
        c.executescript(SCHEMA_V2)
    return store


def _jsonl(*objs: dict) -> bytes:
    """Encode multiple dicts as newline-separated JSONL bytes."""
    return b"\n".join(json.dumps(o).encode() for o in objs) + b"\n"


def _make_mock_process(
    stdout_bytes: bytes,
    returncode: int = 0,
    stderr_bytes: bytes = b"",
):
    """Build an asyncio mock process that yields stdout_bytes line-by-line."""

    class FakeStreamReader:
        def __init__(self, data: bytes) -> None:
            self._lines = iter(data.splitlines(keepends=True))

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._lines)
            except StopIteration:
                raise StopAsyncIteration

    mock_proc = MagicMock()
    mock_proc.stdin = AsyncMock()
    mock_proc.stdin.write = MagicMock()
    mock_proc.stdin.drain = AsyncMock()
    mock_proc.stdin.close = MagicMock()
    mock_proc.stdout = FakeStreamReader(stdout_bytes)
    mock_proc.stderr = AsyncMock()
    mock_proc.stderr.read = AsyncMock(return_value=stderr_bytes)
    mock_proc.wait = AsyncMock(return_value=returncode)
    mock_proc.returncode = returncode
    return mock_proc


# ---------------------------------------------------------------------------
# OrchestratorAgentRepo tests
# ---------------------------------------------------------------------------


class TestOrchestratorAgentRepo:
    def test_upsert_and_get_session_id(self, tmp_path: Path) -> None:
        store = _make_state_store(tmp_path)
        repo = OrchestratorAgentRepo(store)
        oid = "oa-test-001"

        # No row yet
        assert repo.get_session_id(oid) is None

        # Insert
        repo.upsert_orchestrator(oid, session_id=None, status="idle")
        assert repo.get_session_id(oid) is None

        # Set session_id
        repo.update_session_id(oid, "claude-sess-abc")
        assert repo.get_session_id(oid) == "claude-sess-abc"

    def test_accumulate_tokens(self, tmp_path: Path) -> None:
        store = _make_state_store(tmp_path)
        repo = OrchestratorAgentRepo(store)
        oid = "oa-tokens-002"

        repo.upsert_orchestrator(oid, status="idle")
        repo.accumulate_tokens(oid, input_tokens=100, output_tokens=50, cost_usd=0.05)
        repo.accumulate_tokens(oid, input_tokens=200, output_tokens=75, cost_usd=0.10)

        with store.conn() as c:
            row = c.execute(
                "SELECT input_tokens, output_tokens, total_cost FROM orchestrator_agents WHERE id=?",
                (oid,),
            ).fetchone()

        assert row["input_tokens"] == 300
        assert row["output_tokens"] == 125
        assert abs(row["total_cost"] - 0.15) < 1e-9

    def test_set_status(self, tmp_path: Path) -> None:
        store = _make_state_store(tmp_path)
        repo = OrchestratorAgentRepo(store)
        oid = "oa-status-003"

        repo.upsert_orchestrator(oid, status="idle")
        repo.set_status(oid, "executing")

        with store.conn() as c:
            row = c.execute(
                "SELECT status FROM orchestrator_agents WHERE id=?", (oid,)
            ).fetchone()
        assert row["status"] == "executing"


# ---------------------------------------------------------------------------
# _parse_content_blocks helper
# ---------------------------------------------------------------------------


class TestParseContentBlocks:
    def test_text_block(self) -> None:
        blocks = _parse_content_blocks([{"type": "text", "text": "hello world"}])
        assert len(blocks) == 1
        assert isinstance(blocks[0], TextBlock)
        assert blocks[0].text == "hello world"

    def test_thinking_block(self) -> None:
        blocks = _parse_content_blocks([{"type": "thinking", "thinking": "I am pondering"}])
        assert len(blocks) == 1
        assert isinstance(blocks[0], ThinkingBlock)
        assert blocks[0].thinking == "I am pondering"

    def test_tool_use_block(self) -> None:
        blocks = _parse_content_blocks(
            [{"type": "tool_use", "id": "tu-1", "name": "Bash", "input": {"command": "ls"}}]
        )
        assert len(blocks) == 1
        b = blocks[0]
        assert isinstance(b, ToolUseBlock)
        assert b.id == "tu-1"
        assert b.name == "Bash"
        assert b.input == {"command": "ls"}

    def test_mixed_blocks(self) -> None:
        raw = [
            {"type": "thinking", "thinking": "Let me think"},
            {"type": "text", "text": "Here is my answer"},
            {"type": "tool_use", "id": "tu-2", "name": "Read", "input": {"file_path": "/tmp/x"}},
        ]
        blocks = _parse_content_blocks(raw)
        assert isinstance(blocks[0], ThinkingBlock)
        assert isinstance(blocks[1], TextBlock)
        assert isinstance(blocks[2], ToolUseBlock)

    def test_unknown_type_is_skipped(self) -> None:
        blocks = _parse_content_blocks([{"type": "unknown_future_type", "data": 42}])
        assert len(blocks) == 0

    def test_empty_list(self) -> None:
        assert _parse_content_blocks([]) == []


# ---------------------------------------------------------------------------
# ClaudeSDKClient — session management
# ---------------------------------------------------------------------------


class TestClaudeSDKClientSessions:
    def test_create_session_registers_in_db(self, tmp_path: Path) -> None:
        store = _make_state_store(tmp_path)
        oid = "oa-create-001"
        client = ClaudeSDKClient(store, oid, cwd=str(tmp_path))

        returned_id = client.create_session(system_prompt="You are helpful.", cwd=str(tmp_path))
        assert returned_id == oid

        # Row should exist with no session_id yet
        repo = OrchestratorAgentRepo(store)
        assert repo.get_session_id(oid) is None

    def test_resume_session_loads_existing_id(self, tmp_path: Path) -> None:
        store = _make_state_store(tmp_path)
        oid = "oa-resume-001"
        repo = OrchestratorAgentRepo(store)
        repo.upsert_orchestrator(oid, session_id="existing-sess-xyz", status="idle")

        client = ClaudeSDKClient(store, oid)
        result = client.resume_session("existing-sess-xyz")

        assert result is True
        assert client.session_id == "existing-sess-xyz"

    def test_resume_session_creates_row_if_missing(self, tmp_path: Path) -> None:
        store = _make_state_store(tmp_path)
        oid = "oa-resume-002"

        client = ClaudeSDKClient(store, oid)
        result = client.resume_session("brand-new-sess")

        assert result is True
        assert client.session_id == "brand-new-sess"

        # Verify persisted in DB
        repo = OrchestratorAgentRepo(store)
        assert repo.get_session_id(oid) == "brand-new-sess"

    def test_resume_session_updates_if_different(self, tmp_path: Path) -> None:
        store = _make_state_store(tmp_path)
        oid = "oa-resume-003"
        repo = OrchestratorAgentRepo(store)
        repo.upsert_orchestrator(oid, session_id="old-sess", status="idle")

        client = ClaudeSDKClient(store, oid)
        client.resume_session("new-sess")

        assert client.session_id == "new-sess"
        assert repo.get_session_id(oid) == "new-sess"


# ---------------------------------------------------------------------------
# ClaudeSDKClient.send_message — subprocess mocking
# ---------------------------------------------------------------------------


class TestClaudeSDKClientSendMessage:
    """Tests for the streaming pipeline using mocked subprocesses."""

    def _make_client(self, tmp_path: Path, orchestrator_id: str = "oa-msg-001"):
        store = _make_state_store(tmp_path)
        repo = OrchestratorAgentRepo(store)
        repo.upsert_orchestrator(orchestrator_id, status="idle")
        client = ClaudeSDKClient(store, orchestrator_id, cwd=str(tmp_path))
        return client, store

    def _collect(self, client: ClaudeSDKClient, message: str, stdout: bytes):
        """Run send_message with mocked subprocess and collect all events."""

        async def _run():
            mock_proc = _make_mock_process(stdout)
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                events = []
                async for event in client.send_message(message):
                    events.append(event)
            return events

        return asyncio.run(_run())

    def test_yields_text_block(self, tmp_path: Path) -> None:
        client, _ = self._make_client(tmp_path)
        stdout = _jsonl(
            {"type": "system", "subtype": "init", "session_id": "s-abc"},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Hello there"}],
                },
            },
            {"type": "result", "subtype": "success", "result": "Hello there", "session_id": "s-abc", "usage": {}},
        )
        events = self._collect(client, "Hi", stdout)
        text_events = [e for e in events if isinstance(e, TextBlock)]
        assert len(text_events) == 1
        assert text_events[0].text == "Hello there"

    def test_yields_thinking_block(self, tmp_path: Path) -> None:
        client, _ = self._make_client(tmp_path, "oa-think-001")
        stdout = _jsonl(
            {"type": "system", "subtype": "init", "session_id": "s-def"},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "thinking", "thinking": "deep thoughts"}],
                },
            },
            {"type": "result", "subtype": "success", "session_id": "s-def", "usage": {}},
        )
        events = self._collect(client, "Think about X", stdout)
        thinking_events = [e for e in events if isinstance(e, ThinkingBlock)]
        assert len(thinking_events) == 1
        assert thinking_events[0].thinking == "deep thoughts"

    def test_yields_tool_use_block(self, tmp_path: Path) -> None:
        client, _ = self._make_client(tmp_path, "oa-tool-001")
        stdout = _jsonl(
            {"type": "system", "subtype": "init", "session_id": "s-ghi"},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tu-99",
                            "name": "Bash",
                            "input": {"command": "echo hi"},
                        }
                    ],
                },
            },
            {"type": "result", "subtype": "success", "session_id": "s-ghi", "usage": {}},
        )
        events = self._collect(client, "Run a command", stdout)
        tool_events = [e for e in events if isinstance(e, ToolUseBlock)]
        assert len(tool_events) == 1
        assert tool_events[0].name == "Bash"
        assert tool_events[0].input == {"command": "echo hi"}

    def test_result_message_is_last(self, tmp_path: Path) -> None:
        client, _ = self._make_client(tmp_path, "oa-result-001")
        stdout = _jsonl(
            {"type": "system", "subtype": "init", "session_id": "s-jkl"},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "done"}],
                },
            },
            {
                "type": "result",
                "subtype": "success",
                "result": "done",
                "session_id": "s-jkl",
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "cost_usd": 0.003,
            },
        )
        events = self._collect(client, "Do something", stdout)
        assert isinstance(events[-1], ResultMessage)
        result = events[-1]
        assert result.subtype == "success"
        assert result.input_tokens == 10
        assert result.output_tokens == 5
        assert result.cost_usd == pytest.approx(0.003)

    def test_session_id_persisted_from_init(self, tmp_path: Path) -> None:
        client, store = self._make_client(tmp_path, "oa-persist-001")
        stdout = _jsonl(
            {"type": "system", "subtype": "init", "session_id": "new-sess-xyz"},
            {
                "type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
            },
            {"type": "result", "subtype": "success", "session_id": "new-sess-xyz", "usage": {}},
        )
        self._collect(client, "Hello", stdout)

        assert client.session_id == "new-sess-xyz"
        repo = OrchestratorAgentRepo(store)
        assert repo.get_session_id("oa-persist-001") == "new-sess-xyz"

    def test_tokens_accumulated_after_stream(self, tmp_path: Path) -> None:
        client, store = self._make_client(tmp_path, "oa-tokens-stream-001")
        stdout = _jsonl(
            {"type": "system", "subtype": "init", "session_id": "s-tok"},
            {
                "type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
            },
            {
                "type": "result",
                "subtype": "success",
                "session_id": "s-tok",
                "usage": {
                    "input_tokens": 200,
                    "output_tokens": 80,
                },
                "cost_usd": 0.025,
            },
        )
        self._collect(client, "hi", stdout)

        with store.conn() as c:
            row = c.execute(
                "SELECT input_tokens, output_tokens, total_cost FROM orchestrator_agents WHERE id=?",
                ("oa-tokens-stream-001",),
            ).fetchone()

        assert row["input_tokens"] == 200
        assert row["output_tokens"] == 80
        assert abs(row["total_cost"] - 0.025) < 1e-9

    def test_resume_uses_session_flag_in_command(self, tmp_path: Path) -> None:
        """Verify --resume <id> appears in the spawned command when a session_id exists."""
        store = _make_state_store(tmp_path)
        oid = "oa-resume-cmd-001"
        repo = OrchestratorAgentRepo(store)
        repo.upsert_orchestrator(oid, session_id="resume-me", status="idle")

        client = ClaudeSDKClient(store, oid, cwd=str(tmp_path))
        client.resume_session("resume-me")

        captured_cmd: list[list[str]] = []

        async def _run():
            stdout = _jsonl(
                {"type": "system", "subtype": "init", "session_id": "resume-me"},
                {
                    "type": "assistant",
                    "message": {"role": "assistant", "content": [{"type": "text", "text": "resumed"}]},
                },
                {"type": "result", "subtype": "success", "session_id": "resume-me", "usage": {}},
            )
            mock_proc = _make_mock_process(stdout)

            async def mock_exec(*args, **kwargs):
                captured_cmd.append(list(args))
                return mock_proc

            with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
                async for _ in client.send_message("continue"):
                    pass

        asyncio.run(_run())

        assert len(captured_cmd) == 1
        cmd_flat = captured_cmd[0]
        assert "--resume" in cmd_flat
        idx = cmd_flat.index("--resume")
        assert cmd_flat[idx + 1] == "resume-me"

    def test_non_zero_exit_does_not_raise(self, tmp_path: Path) -> None:
        """A non-zero subprocess exit code should be logged but not raise."""
        client, _ = self._make_client(tmp_path, "oa-exit-001")
        stdout = _jsonl(
            {"type": "result", "subtype": "error", "error": "something went wrong", "usage": {}},
        )

        async def _run():
            mock_proc = _make_mock_process(stdout, returncode=1, stderr_bytes=b"stderr output")
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                events = []
                async for event in client.send_message("bad request"):
                    events.append(event)
            return events

        events = asyncio.run(_run())
        result_events = [e for e in events if isinstance(e, ResultMessage)]
        assert len(result_events) == 1
        assert result_events[0].subtype == "error"

    def test_skips_non_json_lines(self, tmp_path: Path) -> None:
        """Non-JSON lines in stdout (e.g. debug output) should be silently skipped."""
        client, _ = self._make_client(tmp_path, "oa-skip-001")
        stdout = (
            b"INFO: starting up\n"
            + _jsonl(
                {"type": "system", "subtype": "init", "session_id": "s-skip"},
                {
                    "type": "assistant",
                    "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
                },
                {"type": "result", "subtype": "success", "session_id": "s-skip", "usage": {}},
            )
        )
        events = self._collect(client, "skip test", stdout)
        # Should still get text + result
        text_events = [e for e in events if isinstance(e, TextBlock)]
        result_events = [e for e in events if isinstance(e, ResultMessage)]
        assert len(text_events) == 1
        assert len(result_events) == 1

    def test_multiple_content_blocks_in_one_assistant_message(self, tmp_path: Path) -> None:
        """An assistant message with thinking + text should yield both blocks."""
        client, _ = self._make_client(tmp_path, "oa-multi-001")
        stdout = _jsonl(
            {"type": "system", "subtype": "init", "session_id": "s-multi"},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "I think..."},
                        {"type": "text", "text": "Here is my answer"},
                    ],
                },
            },
            {"type": "result", "subtype": "success", "session_id": "s-multi", "usage": {}},
        )
        events = self._collect(client, "complex question", stdout)
        thinking = [e for e in events if isinstance(e, ThinkingBlock)]
        text = [e for e in events if isinstance(e, TextBlock)]
        assert len(thinking) == 1
        assert len(text) == 1
        assert events.index(thinking[0]) < events.index(text[0]), "ThinkingBlock should precede TextBlock"
