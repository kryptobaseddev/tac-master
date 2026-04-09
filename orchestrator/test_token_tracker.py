"""Regression tests for token_tracker path fix (T032).

T029 audit found two bugs in discover_phase_files():
  1. Glob used "cc_raw_output.jsonl" but agent.py writes "raw_output.jsonl"
  2. Base dir used worktree_path/"agents"/adw_id but actual files live at
     repo_root/"agents"/adw_id where repo_root = worktree_path.parent.parent

These tests verify the corrected behaviour:
  - raw_output.jsonl at the correct repo-root path IS found and parsed
  - token_ledger is populated with non-zero token counts and cost_usd
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Ensure orchestrator package is on path when running from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator.token_tracker import (
    PhaseAttribution,
    TokenTracker,
    Usage,
    parse_jsonl_file,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_ASSISTANT_LINE = json.dumps({
    "type": "assistant",
    "message": {
        "model": "claude-sonnet-4-5",
        "usage": {
            "input_tokens": 1000,
            "output_tokens": 500,
            "cache_creation_input_tokens": 200,
            "cache_read_input_tokens": 100,
        },
    },
})

SAMPLE_RESULT_LINE = json.dumps({
    "type": "result",
    "total_cost_usd": 0.0125,
    "usage": {
        "input_tokens": 1000,
        "output_tokens": 500,
    },
})


def _make_fake_pricebook(tmp_path: Path) -> Path:
    """Write a minimal model_prices.yaml so PriceBook doesn't crash."""
    prices_yaml = """\
prices:
  default:
    input: 15.0
    output: 75.0
    cache_write: 18.75
    cache_read: 1.50
  claude-sonnet-4-5:
    input: 3.0
    output: 15.0
    cache_write: 3.75
    cache_read: 0.30
"""
    p = tmp_path / "model_prices.yaml"
    p.write_text(prices_yaml)
    return p


def _make_fake_store(db_path: Path):
    """Create a minimal StateStore-like object backed by a real SQLite DB."""
    # Build just enough of the interface token_tracker needs
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    class _FakeStore:
        def conn(self):
            return conn

        def add_tokens(self, repo_url: str, n: int):
            pass  # not testing budget_usage here

    return _FakeStore()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestParseJsonlFile:
    """parse_jsonl_file should extract tokens from raw_output.jsonl content."""

    def test_reads_assistant_and_result_lines(self, tmp_path):
        f = tmp_path / "raw_output.jsonl"
        f.write_text(SAMPLE_ASSISTANT_LINE + "\n" + SAMPLE_RESULT_LINE + "\n")

        usage = parse_jsonl_file(f)

        assert usage.input_tokens == 1000
        assert usage.output_tokens == 500
        assert usage.cache_creation_input_tokens == 200
        assert usage.cache_read_input_tokens == 100
        # total_cost_usd from result line takes precedence
        assert usage.cost_usd == pytest.approx(0.0125)

    def test_missing_file_returns_zero_usage(self, tmp_path):
        usage = parse_jsonl_file(tmp_path / "nonexistent.jsonl")
        assert usage.total_tokens == 0
        assert usage.cost_usd == 0.0


class TestDiscoverPhaseFiles:
    """discover_phase_files must look in repo_root/agents, not worktree/agents."""

    def _build_tree(self, tmp_path: Path, adw_id: str, phase: str) -> Path:
        """
        Build:
          <tmp_path>/
            repos/
              myrepo/
                agents/<adw_id>/<phase>/raw_output.jsonl   <- WRITER path
                trees/<adw_id>/                            <- worktree path
        """
        repo_root = tmp_path / "repos" / "myrepo"
        agent_dir = repo_root / "agents" / adw_id / phase
        agent_dir.mkdir(parents=True)
        jsonl = agent_dir / "raw_output.jsonl"
        jsonl.write_text(SAMPLE_ASSISTANT_LINE + "\n" + SAMPLE_RESULT_LINE + "\n")

        worktree = repo_root / "trees" / adw_id
        worktree.mkdir(parents=True)

        return worktree

    def test_finds_file_at_repo_root_path(self, tmp_path):
        adw_id = "abc12345"
        phase = "sdlc_planner"
        worktree = self._build_tree(tmp_path, adw_id, phase)

        pricebook = _make_fake_pricebook(tmp_path)
        db_path = tmp_path / "state.sqlite"
        store = _make_fake_store(db_path)
        tracker = TokenTracker(store, pricebook)

        attributions = tracker.discover_phase_files(worktree, adw_id)

        assert len(attributions) == 1, (
            "Expected exactly 1 PhaseAttribution; "
            f"got {len(attributions)} — likely still using old worktree path or cc_ prefix"
        )
        att = attributions[0]
        assert att.phase_name == phase
        assert "raw_output.jsonl" in att.file_path
        assert "cc_raw_output" not in att.file_path
        assert att.usage.input_tokens == 1000
        assert att.usage.output_tokens == 500

    def test_does_not_find_file_in_worktree_subtree(self, tmp_path):
        """Ensure a cc_raw_output.jsonl inside the worktree subtree is NOT found
        (wrong filename means old path never accidentally matches)."""
        adw_id = "def67890"
        # Write with the OLD (wrong) filename inside the OLD worktree location
        repo_root = tmp_path / "repos" / "myrepo"
        worktree = repo_root / "trees" / adw_id
        wrong_dir = worktree / "agents" / adw_id / "some_phase"
        wrong_dir.mkdir(parents=True)
        (wrong_dir / "cc_raw_output.jsonl").write_text(SAMPLE_ASSISTANT_LINE + "\n")

        pricebook = _make_fake_pricebook(tmp_path)
        db_path = tmp_path / "state.sqlite"
        store = _make_fake_store(db_path)
        tracker = TokenTracker(store, pricebook)

        # No agents/<adw_id> dir at repo_root level, so result should be empty
        attributions = tracker.discover_phase_files(worktree, adw_id)
        assert attributions == []

    def test_multiple_phases_all_found(self, tmp_path):
        adw_id = "cafe0001"
        phases = ["issue_classifier", "sdlc_planner", "sdlc_implementor"]
        repo_root = tmp_path / "repos" / "myrepo"

        for phase in phases:
            d = repo_root / "agents" / adw_id / phase
            d.mkdir(parents=True, exist_ok=True)
            (d / "raw_output.jsonl").write_text(
                SAMPLE_ASSISTANT_LINE + "\n" + SAMPLE_RESULT_LINE + "\n"
            )

        worktree = repo_root / "trees" / adw_id
        worktree.mkdir(parents=True, exist_ok=True)

        pricebook = _make_fake_pricebook(tmp_path)
        db_path = tmp_path / "state.sqlite"
        store = _make_fake_store(db_path)
        tracker = TokenTracker(store, pricebook)

        attributions = tracker.discover_phase_files(worktree, adw_id)
        found_phases = {a.phase_name for a in attributions}
        assert found_phases == set(phases)


class TestAttributeRun:
    """attribute_run must insert rows into token_ledger with non-zero tokens/cost."""

    def test_inserts_token_ledger_rows(self, tmp_path):
        adw_id = "feed1234"
        phase = "sdlc_planner"
        repo_url = "https://github.com/test/repo"

        repo_root = tmp_path / "repos" / "myrepo"
        agent_dir = repo_root / "agents" / adw_id / phase
        agent_dir.mkdir(parents=True)
        (agent_dir / "raw_output.jsonl").write_text(
            SAMPLE_ASSISTANT_LINE + "\n" + SAMPLE_RESULT_LINE + "\n"
        )
        worktree = repo_root / "trees" / adw_id
        worktree.mkdir(parents=True)

        db_path = tmp_path / "state.sqlite"
        store = _make_fake_store(db_path)

        # Pre-create the runs table with a row so UPDATE in attribute_run doesn't fail
        with store.conn() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS runs "
                "(adw_id TEXT PRIMARY KEY, repo_url TEXT, worktree_path TEXT, tokens_used INTEGER DEFAULT 0)"
            )
            c.execute(
                "INSERT INTO runs VALUES (?, ?, ?, 0)",
                (adw_id, repo_url, str(worktree)),
            )

        pricebook = _make_fake_pricebook(tmp_path)
        tracker = TokenTracker(store, pricebook)

        usage = tracker.attribute_run(adw_id, worktree, repo_url)

        assert usage.input_tokens == 1000
        assert usage.output_tokens == 500
        assert usage.cost_usd > 0, "cost_usd must be non-zero after attribution"

        with store.conn() as c:
            rows = c.execute("SELECT * FROM token_ledger").fetchall()

        assert len(rows) == 1
        row = rows[0]
        assert row["adw_id"] == adw_id
        assert row["repo_url"] == repo_url
        assert row["phase_name"] == phase
        assert row["input_tokens"] == 1000
        assert row["output_tokens"] == 500
        assert row["cost_usd"] > 0

    def test_idempotent_on_second_call(self, tmp_path):
        adw_id = "idm00001"
        phase = "branch_generator"
        repo_url = "https://github.com/test/repo"

        repo_root = tmp_path / "repos" / "myrepo"
        agent_dir = repo_root / "agents" / adw_id / phase
        agent_dir.mkdir(parents=True)
        (agent_dir / "raw_output.jsonl").write_text(
            SAMPLE_ASSISTANT_LINE + "\n" + SAMPLE_RESULT_LINE + "\n"
        )
        worktree = repo_root / "trees" / adw_id
        worktree.mkdir(parents=True)

        db_path = tmp_path / "state.sqlite"
        store = _make_fake_store(db_path)
        with store.conn() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS runs "
                "(adw_id TEXT PRIMARY KEY, repo_url TEXT, worktree_path TEXT, tokens_used INTEGER DEFAULT 0)"
            )
            c.execute("INSERT INTO runs VALUES (?, ?, ?, 0)", (adw_id, repo_url, str(worktree)))

        pricebook = _make_fake_pricebook(tmp_path)
        tracker = TokenTracker(store, pricebook)

        tracker.attribute_run(adw_id, worktree, repo_url)
        tracker.attribute_run(adw_id, worktree, repo_url)  # second call, same data

        with store.conn() as c:
            count = c.execute("SELECT COUNT(*) FROM token_ledger").fetchone()[0]

        assert count == 1, "Idempotency broken: duplicate rows inserted"
