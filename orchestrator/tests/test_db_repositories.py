"""Integration tests for database repositories (T114).

Tests the db_repositories.py API using real temporary SQLite files with WAL mode.
No mocking, no :memory: databases — each test creates a fresh temp file and runs
the full migration to exercise real database operations.

Test scenarios:
a. Orchestrator session lifecycle: create → get → archive → verify archived
b. Agent instance: create → list_for_orchestrator → complete → verify status
c. Chat messages: insert multiple → get_history → verify ASC order → update_summary
d. System logs: insert thinking/tool_use/app types → get_for_session → verify types
e. Migration idempotency: run migrate_db.py twice → no exception → verify table count

Each test:
- Creates a real temp SQLite file
- Runs migration SQL to create tables
- Exercises the repo methods
- Cleans up (deletes temp file)
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

from orchestrator.db_repositories import (
    OrchestratorAgentRepo,
    AgentInstanceRepo,
    ChatMessageRepo,
    SystemLogRepo,
)
from orchestrator.migrate_db import migrate


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def temp_db_path():
    """Create a temporary SQLite file path with proper cleanup.

    Yields the path; caller is responsible for creating and using the file.
    Cleanup happens automatically after the test.
    """
    temp_path = Path(tempfile.mktemp(suffix=".sqlite"))
    yield temp_path
    # Cleanup: remove the database and associated WAL files
    if temp_path.exists():
        temp_path.unlink()
    if temp_path.with_suffix(".sqlite-wal").exists():
        temp_path.with_suffix(".sqlite-wal").unlink()
    if temp_path.with_suffix(".sqlite-shm").exists():
        temp_path.with_suffix(".sqlite-shm").unlink()


@pytest.fixture
def initialized_db(temp_db_path):
    """Create a temporary database, run migration, and return the path.

    This fixture:
    1. Creates the temp file
    2. Runs the full migration (migrate_db.py)
    3. Returns the path for test use
    4. Cleanup happens via temp_db_path fixture
    """
    migrate(temp_db_path)
    return temp_db_path


# ============================================================================
# Test a: Orchestrator Session Lifecycle
# ============================================================================


def test_orchestrator_session_lifecycle(initialized_db):
    """Test: create → get → archive → verify archived.

    Validates that an orchestrator session can be:
    - Created with initial status 'idle'
    - Retrieved by ID
    - Archived (soft-deleted)
    - Verified as archived (archived=1)
    """
    conn = sqlite3.connect(str(initialized_db))
    conn.row_factory = sqlite3.Row

    try:
        # Create a new orchestrator agent
        result = OrchestratorAgentRepo.create(
            conn,
            session_id="sdk-session-001",
            status="idle",
            system_prompt="You are the orchestrator.",
            metadata={"model": "claude-opus-4-5", "version": "1.0"},
            working_dir="/mnt/projects",
        )
        agent_id = result["id"]
        assert agent_id.startswith("oa-")
        assert result["status"] == "idle"
        assert result["archived"] == 0
        assert result["metadata"]["model"] == "claude-opus-4-5"
        conn.commit()

        # Retrieve the agent
        fetched = OrchestratorAgentRepo.get(conn, agent_id)
        assert fetched is not None
        assert fetched["id"] == agent_id
        assert fetched["session_id"] == "sdk-session-001"
        assert fetched["status"] == "idle"
        assert fetched["archived"] == 0

        # Archive the agent
        OrchestratorAgentRepo.archive(conn, agent_id)
        conn.commit()

        # Verify archived
        archived = OrchestratorAgentRepo.get(conn, agent_id)
        assert archived is not None
        assert archived["archived"] == 1
        assert archived["updated_at"] >= result["created_at"]
    finally:
        conn.close()


# ============================================================================
# Test b: Agent Instance Lifecycle
# ============================================================================


def test_agent_instance_lifecycle(initialized_db):
    """Test: create orchestrator → create agent instance → list → complete.

    Validates that:
    - An orchestrator session is created first
    - Agent instances can be created under it
    - Instances can be listed for the orchestrator
    - Instances can be marked complete (archived=1, status='complete')
    """
    conn = sqlite3.connect(str(initialized_db))
    conn.row_factory = sqlite3.Row

    try:
        # Create orchestrator session
        orch = OrchestratorAgentRepo.create(conn)
        orch_id = orch["id"]
        conn.commit()

        # Create agent instances
        ai1 = AgentInstanceRepo.create(
            conn,
            orchestrator_agent_id=orch_id,
            name="implementor_1",
            model="claude-sonnet-4-5",
            system_prompt="You implement code.",
            working_dir="/tmp/work1",
            metadata={"task": "implement_feature"},
        )
        ai1_id = ai1["id"]
        assert ai1_id.startswith("ai-")
        assert ai1["status"] == "idle"
        assert ai1["archived"] == 0
        conn.commit()

        ai2 = AgentInstanceRepo.create(
            conn,
            orchestrator_agent_id=orch_id,
            name="reviewer_1",
            model="claude-opus-4-5",
            adw_id="adw-run-123",
            phase="plan_iso",
        )
        ai2_id = ai2["id"]
        conn.commit()

        # List instances for the orchestrator
        instances = AgentInstanceRepo.list_for_orchestrator(conn, orch_id)
        assert len(instances) == 2
        assert instances[0]["id"] == ai1_id
        assert instances[1]["id"] == ai2_id
        # Should be in created_at ASC order
        assert instances[0]["created_at"] <= instances[1]["created_at"]

        # Complete the first instance
        AgentInstanceRepo.complete(conn, ai1_id, status="complete")
        conn.commit()

        # Verify completed
        completed = AgentInstanceRepo.get(conn, ai1_id)
        assert completed["status"] == "complete"
        assert completed["archived"] == 1

        # Second instance should still be active
        active = AgentInstanceRepo.get(conn, ai2_id)
        assert active["archived"] == 0
    finally:
        conn.close()


# ============================================================================
# Test c: Chat Messages History
# ============================================================================


def test_chat_messages_history_and_summary(initialized_db):
    """Test: insert multiple messages → get_history → verify ASC order → update_summary.

    Validates that:
    - Multiple chat messages can be inserted for a session
    - History is returned in created_at ASC order (chronological)
    - Summary can be updated asynchronously
    """
    conn = sqlite3.connect(str(initialized_db))
    conn.row_factory = sqlite3.Row

    try:
        # Create orchestrator session
        orch = OrchestratorAgentRepo.create(conn)
        orch_id = orch["id"]
        conn.commit()

        # Insert multiple messages with slight delays to ensure ordering
        msg1 = ChatMessageRepo.insert(
            conn,
            orchestrator_agent_id=orch_id,
            sender_type="user",
            receiver_type="orchestrator",
            message="Hello, please help me.",
            metadata={"tokens": 10},
            session_id="session-001",
        )
        msg1_id = msg1["id"]
        assert msg1_id.startswith("cm-")
        assert msg1["summary"] is None
        conn.commit()

        # Small delay to ensure different timestamp
        time.sleep(0.01)

        msg2 = ChatMessageRepo.insert(
            conn,
            orchestrator_agent_id=orch_id,
            sender_type="orchestrator",
            receiver_type="agent",
            message="I will spawn an agent.",
            metadata={"tokens": 20},
            session_id="session-001",
        )
        msg2_id = msg2["id"]
        conn.commit()

        time.sleep(0.01)

        msg3 = ChatMessageRepo.insert(
            conn,
            orchestrator_agent_id=orch_id,
            sender_type="agent",
            receiver_type="orchestrator",
            message="Task completed.",
            agent_id="ai-test-123",
            metadata={"tokens": 15},
            session_id="session-001",
        )
        msg3_id = msg3["id"]
        conn.commit()

        # Get history and verify ASC order
        history = ChatMessageRepo.get_history(conn, orch_id, limit=50)
        assert len(history) == 3
        assert history[0]["id"] == msg1_id
        assert history[1]["id"] == msg2_id
        assert history[2]["id"] == msg3_id
        # Verify chronological order
        assert history[0]["created_at"] <= history[1]["created_at"]
        assert history[1]["created_at"] <= history[2]["created_at"]

        # Verify sender/receiver types
        assert history[0]["sender_type"] == "user"
        assert history[0]["receiver_type"] == "orchestrator"
        assert history[1]["sender_type"] == "orchestrator"
        assert history[2]["sender_type"] == "agent"
        assert history[2]["agent_id"] == "ai-test-123"

        # Update summary for first message
        ChatMessageRepo.update_summary(conn, msg1_id, "User requested help.")
        conn.commit()

        # Verify summary was updated
        updated = ChatMessageRepo.get_history(conn, orch_id)
        msg1_after = [m for m in updated if m["id"] == msg1_id][0]
        assert msg1_after["summary"] == "User requested help."
    finally:
        conn.close()


# ============================================================================
# Test d: System Logs (Thinking, Tool Use, App Events)
# ============================================================================


def test_system_logs_multiple_types(initialized_db):
    """Test: insert thinking/tool_use/app log types → get_for_session → verify types.

    Validates that:
    - Thinking blocks can be logged with log_type='thinking'
    - Tool use blocks can be logged with log_type='tool_use'
    - App-level events can be logged (no orchestrator/agent FK)
    - Logs are retrievable for a session in timestamp ASC order
    """
    conn = sqlite3.connect(str(initialized_db))
    conn.row_factory = sqlite3.Row

    try:
        # Create orchestrator and agent for context
        orch = OrchestratorAgentRepo.create(conn)
        orch_id = orch["id"]
        conn.commit()

        ai = AgentInstanceRepo.create(
            conn,
            orchestrator_agent_id=orch_id,
            name="test_agent",
        )
        ai_id = ai["id"]
        conn.commit()

        session_id = "session-002"

        # Insert thinking block log
        log1 = SystemLogRepo.insert(
            conn,
            level="DEBUG",
            message="Analyzing problem structure...",
            metadata={"blocks": ["analyze", "plan"]},
            orchestrator_agent_id=orch_id,
            agent_id=ai_id,
            session_id=session_id,
            log_type="thinking",
            event_type="ThinkingBlock",
        )
        log1_id = log1["id"]
        assert log1_id.startswith("sl-")
        assert log1["log_type"] == "thinking"
        conn.commit()

        time.sleep(0.01)

        # Insert tool use log
        log2 = SystemLogRepo.insert(
            conn,
            level="INFO",
            message="Executing bash command",
            metadata={
                "tool_name": "bash",
                "input": {"command": "ls -la /tmp"},
                "output": "total 24\n...",
            },
            orchestrator_agent_id=orch_id,
            agent_id=ai_id,
            session_id=session_id,
            log_type="tool_use",
            event_type="ToolUseBlock",
        )
        log2_id = log2["id"]
        assert log2["log_type"] == "tool_use"
        conn.commit()

        time.sleep(0.01)

        # Insert app-level event (no agent/orchestrator FK)
        log3 = SystemLogRepo.insert(
            conn,
            level="INFO",
            message="Migration complete",
            metadata={"tables_created": 4, "duration_ms": 42},
            log_type="app",
            event_type="MigrationComplete",
        )
        log3_id = log3["id"]
        assert log3["orchestrator_agent_id"] is None
        assert log3["agent_id"] is None
        assert log3["log_type"] == "app"
        conn.commit()

        # Get logs for session (should have log1 and log2, not log3)
        session_logs = SystemLogRepo.get_for_session(conn, session_id, limit=500)
        assert len(session_logs) == 2
        assert session_logs[0]["id"] == log1_id
        assert session_logs[1]["id"] == log2_id
        # Verify order (should be timestamp ASC)
        assert session_logs[0]["timestamp"] <= session_logs[1]["timestamp"]

        # Verify types
        assert session_logs[0]["log_type"] == "thinking"
        assert session_logs[1]["log_type"] == "tool_use"

        # Verify JSON payload deserialization
        assert session_logs[0]["payload"]["blocks"] == ["analyze", "plan"]
        assert session_logs[1]["payload"]["tool_name"] == "bash"

        # Update summary for thinking log
        SystemLogRepo.update_summary(conn, log1_id, "Agent analyzed problem structure and planned approach.")
        conn.commit()

        # Verify summary update
        logs_after = SystemLogRepo.get_for_session(conn, session_id)
        log1_after = [l for l in logs_after if l["id"] == log1_id][0]
        assert log1_after["summary"] == "Agent analyzed problem structure and planned approach."
    finally:
        conn.close()


# ============================================================================
# Test e: Migration Idempotency
# ============================================================================


def test_migration_idempotency(temp_db_path):
    """Test: run migrate_db.py twice → no exception → verify table count unchanged.

    Validates that:
    - Migration can be run on a fresh database
    - Migration can be run again on the same database without errors
    - Table count remains consistent after second run
    - CREATE TABLE IF NOT EXISTS protects existing data
    """
    # First migration
    migrate(temp_db_path)

    # Count tables after first migration
    conn1 = sqlite3.connect(str(temp_db_path))
    before_tables = {
        r[0]
        for r in conn1.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    }
    conn1.close()

    expected_v2_tables = {
        "schema_version",
        "orchestrator_agents",
        "agent_instances",
        "chat_messages",
        "system_logs",
    }
    assert expected_v2_tables.issubset(before_tables), (
        f"Missing v2 tables. Expected subset {expected_v2_tables} "
        f"in {before_tables}"
    )

    # Second migration (idempotent)
    migrate(temp_db_path)

    # Count tables after second migration
    conn2 = sqlite3.connect(str(temp_db_path))
    after_tables = {
        r[0]
        for r in conn2.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    }
    conn2.close()

    # Verify tables unchanged
    assert before_tables == after_tables, (
        f"Tables changed after second migration. "
        f"Before: {before_tables}, After: {after_tables}"
    )

    # Verify schema_version entry exists
    conn3 = sqlite3.connect(str(temp_db_path))
    row = conn3.execute(
        "SELECT version, description FROM schema_version WHERE version=2"
    ).fetchone()
    assert row is not None
    assert row[0] == 2
    conn3.close()

    # Cleanup done by temp_db_path fixture
    temp_db_path_obj = Path(temp_db_path)
    if temp_db_path_obj.exists():
        temp_db_path_obj.unlink()
    if temp_db_path_obj.with_suffix(".sqlite-wal").exists():
        temp_db_path_obj.with_suffix(".sqlite-wal").unlink()
    if temp_db_path_obj.with_suffix(".sqlite-shm").exists():
        temp_db_path_obj.with_suffix(".sqlite-shm").unlink()


# ============================================================================
# Additional: Foreign Key Constraint Test
# ============================================================================


def test_foreign_key_constraints(initialized_db):
    """Test: foreign key constraints are enforced.

    Validates that:
    - Attempting to create an agent_instance with invalid orchestrator_agent_id raises
    - Valid FKs work correctly
    """
    conn = sqlite3.connect(str(initialized_db))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")

    try:
        # Attempting to insert with invalid FK should fail
        with pytest.raises(sqlite3.IntegrityError):
            AgentInstanceRepo.create(
                conn,
                orchestrator_agent_id="oa-invalid-id",
                name="test",
            )
            conn.commit()
    finally:
        conn.close()


# ============================================================================
# Additional: Agent Instance with ADW Tracking
# ============================================================================


def test_agent_instance_adw_tracking(initialized_db):
    """Test: agent instances properly track ADW run ID and PITER phase.

    Validates that:
    - agent_instances.adw_id links to runs table
    - agent_instances.adw_step tracks PITER phase
    - list_for_adw returns instances for a given ADW run
    """
    conn = sqlite3.connect(str(initialized_db))
    conn.row_factory = sqlite3.Row

    try:
        # Create orchestrator
        orch = OrchestratorAgentRepo.create(conn)
        orch_id = orch["id"]
        conn.commit()

        adw_id = "adw-run-456"

        # Create agent instances for the same ADW run
        ai1 = AgentInstanceRepo.create(
            conn,
            orchestrator_agent_id=orch_id,
            adw_id=adw_id,
            phase="plan_iso",
            name="planner",
        )
        ai1_id = ai1["id"]
        conn.commit()

        ai2 = AgentInstanceRepo.create(
            conn,
            orchestrator_agent_id=orch_id,
            adw_id=adw_id,
            phase="build_iso",
            name="builder",
        )
        ai2_id = ai2["id"]
        conn.commit()

        # Create another instance for a different ADW (should not be returned)
        other_adw = "adw-other-789"
        ai3 = AgentInstanceRepo.create(
            conn,
            orchestrator_agent_id=orch_id,
            adw_id=other_adw,
            phase="plan_iso",
            name="other_agent",
        )
        conn.commit()

        # List instances for first ADW
        adw_instances = AgentInstanceRepo.list_for_adw(conn, adw_id)
        assert len(adw_instances) == 2
        assert {ai["id"] for ai in adw_instances} == {ai1_id, ai2_id}

        # Verify phase tracking
        phases = {ai["adw_step"] for ai in adw_instances}
        assert phases == {"plan_iso", "build_iso"}
    finally:
        conn.close()


# ============================================================================
# Additional: Cost and Token Tracking
# ============================================================================


def test_cost_and_token_tracking(initialized_db):
    """Test: orchestrator and agent instances track token usage and costs.

    Validates that:
    - Tokens and costs can be updated
    - Updates accumulate properly
    """
    conn = sqlite3.connect(str(initialized_db))
    conn.row_factory = sqlite3.Row

    try:
        # Create orchestrator
        orch = OrchestratorAgentRepo.create(conn)
        orch_id = orch["id"]
        initial = OrchestratorAgentRepo.get(conn, orch_id)
        assert initial["input_tokens"] == 0
        assert initial["output_tokens"] == 0
        assert initial["total_cost"] == 0.0

        # Update costs
        OrchestratorAgentRepo.update_costs(
            conn,
            orch_id,
            input_tokens=1200,
            output_tokens=450,
            total_cost=0.045,
        )
        conn.commit()

        # Verify update
        updated = OrchestratorAgentRepo.get(conn, orch_id)
        assert updated["input_tokens"] == 1200
        assert updated["output_tokens"] == 450
        assert updated["total_cost"] == 0.045

        # Same test for agent instance
        ai = AgentInstanceRepo.create(conn, orchestrator_agent_id=orch_id)
        ai_id = ai["id"]
        conn.commit()

        AgentInstanceRepo.update_costs(
            conn,
            ai_id,
            input_tokens=800,
            output_tokens=300,
            total_cost=0.030,
        )
        conn.commit()

        ai_updated = AgentInstanceRepo.get(conn, ai_id)
        assert ai_updated["input_tokens"] == 800
        assert ai_updated["output_tokens"] == 300
        assert ai_updated["total_cost"] == 0.030
    finally:
        conn.close()
