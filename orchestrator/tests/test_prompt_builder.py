"""Unit tests for orchestrator.prompt_builder.

Verifies that all four dynamic placeholders are replaced in the rendered
system prompt, and that graceful fallbacks work when external dependencies
(cleo CLI, StateStore) are unavailable.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.prompt_builder import (
    _resolve_active_runs,
    _resolve_available_agents,
    _resolve_available_workflows,
    _resolve_cleo_context,
    build_system_prompt,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_template(tmp_path: Path, body: str) -> Path:
    """Write *body* to a temporary template file and return its path."""
    p = tmp_path / "orchestrator_system.md"
    p.write_text(body, encoding="utf-8")
    return p


def _make_adws_dir(tmp_path: Path, scripts: list[str]) -> Path:
    """Create a fake adws/ directory with the given script stems."""
    adws = tmp_path / "adws"
    adws.mkdir()
    for name in scripts:
        (adws / f"{name}.py").write_text("# stub\n", encoding="utf-8")
    return adws


def _make_agents_dir(tmp_path: Path, commands: list[str]) -> Path:
    """Create a fake .claude/commands/ directory with the given command stems."""
    agents = tmp_path / "commands"
    agents.mkdir()
    for name in commands:
        (agents / f"{name}.md").write_text(f"# {name}\n", encoding="utf-8")
    return agents


# ---------------------------------------------------------------------------
# _resolve_available_workflows
# ---------------------------------------------------------------------------


class TestResolveAvailableWorkflows:
    def test_returns_bullet_list(self, tmp_path: Path) -> None:
        adws = _make_adws_dir(tmp_path, ["adw_build_iso", "adw_plan_iso"])
        result = _resolve_available_workflows(adws)
        assert "- `adw_build_iso`" in result
        assert "- `adw_plan_iso`" in result

    def test_ignores_non_adw_files(self, tmp_path: Path) -> None:
        adws = tmp_path / "adws"
        adws.mkdir()
        (adws / "adw_plan_iso.py").write_text("", encoding="utf-8")
        (adws / "helper.py").write_text("", encoding="utf-8")
        result = _resolve_available_workflows(adws)
        assert "helper" not in result
        assert "adw_plan_iso" in result

    def test_missing_directory_returns_fallback(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent"
        result = _resolve_available_workflows(missing)
        assert "{{AVAILABLE_WORKFLOWS}}" not in result
        assert result  # non-empty fallback

    def test_empty_directory_returns_fallback(self, tmp_path: Path) -> None:
        adws = tmp_path / "adws"
        adws.mkdir()
        result = _resolve_available_workflows(adws)
        assert "{{AVAILABLE_WORKFLOWS}}" not in result
        assert result


# ---------------------------------------------------------------------------
# _resolve_available_agents
# ---------------------------------------------------------------------------


class TestResolveAvailableAgents:
    def test_returns_slash_command_list(self, tmp_path: Path) -> None:
        agents = _make_agents_dir(tmp_path, ["implement", "review", "test"])
        result = _resolve_available_agents(agents)
        assert "- `/implement`" in result
        assert "- `/review`" in result
        assert "- `/test`" in result

    def test_ignores_non_md_files(self, tmp_path: Path) -> None:
        agents = tmp_path / "commands"
        agents.mkdir()
        (agents / "implement.md").write_text("# implement\n", encoding="utf-8")
        (agents / "helper.py").write_text("# helper\n", encoding="utf-8")
        (agents / "README.txt").write_text("readme\n", encoding="utf-8")
        result = _resolve_available_agents(agents)
        assert "helper" not in result
        assert "README" not in result
        assert "implement" in result

    def test_skips_subdirectories(self, tmp_path: Path) -> None:
        agents = tmp_path / "commands"
        agents.mkdir()
        (agents / "feature.md").write_text("# feature\n", encoding="utf-8")
        subdir = agents / "e2e"
        subdir.mkdir()
        (subdir / "test_basic.md").write_text("# test\n", encoding="utf-8")
        result = _resolve_available_agents(agents)
        assert "feature" in result
        assert "test_basic" not in result

    def test_missing_directory_returns_fallback(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent_commands"
        result = _resolve_available_agents(missing)
        assert "{{AVAILABLE_AGENTS}}" not in result
        assert result  # non-empty fallback

    def test_empty_directory_returns_fallback(self, tmp_path: Path) -> None:
        agents = tmp_path / "commands"
        agents.mkdir()
        result = _resolve_available_agents(agents)
        assert "{{AVAILABLE_AGENTS}}" not in result
        assert result

    def test_commands_sorted_alphabetically(self, tmp_path: Path) -> None:
        agents = _make_agents_dir(tmp_path, ["zebra", "alpha", "mango"])
        result = _resolve_available_agents(agents)
        alpha_pos = result.index("alpha")
        mango_pos = result.index("mango")
        zebra_pos = result.index("zebra")
        assert alpha_pos < mango_pos < zebra_pos


# ---------------------------------------------------------------------------
# _resolve_cleo_context
# ---------------------------------------------------------------------------


class TestResolveCleoContext:
    def test_returns_stdout_when_cleo_succeeds(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "## Dashboard\n- T074 pending\n"

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = _resolve_cleo_context()

        mock_run.assert_called_once()
        assert "T074" in result
        assert "{{CLEO_CONTEXT}}" not in result

    def test_fallback_when_cleo_not_found(self) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = _resolve_cleo_context()

        assert "{{CLEO_CONTEXT}}" not in result
        assert result  # non-empty fallback

    def test_fallback_when_cleo_times_out(self) -> None:
        import subprocess

        with patch(
            "subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="cleo", timeout=10)
        ):
            result = _resolve_cleo_context()

        assert "{{CLEO_CONTEXT}}" not in result
        assert result

    def test_fallback_when_cleo_returns_nonzero(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            result = _resolve_cleo_context()

        assert "{{CLEO_CONTEXT}}" not in result
        assert result


# ---------------------------------------------------------------------------
# _resolve_active_runs
# ---------------------------------------------------------------------------


class TestResolveActiveRuns:
    def test_none_state_store_returns_fallback(self) -> None:
        result = _resolve_active_runs(None)
        assert "{{ACTIVE_RUNS}}" not in result
        assert result

    def test_empty_runs_returns_no_active_message(self) -> None:
        store = MagicMock()
        store.list_active_runs.return_value = []
        result = _resolve_active_runs(store)
        assert "No active runs" in result

    def test_formats_run_ids(self) -> None:
        store = MagicMock()
        store.list_active_runs.return_value = [
            {"adw_id": "abc-123", "status": "running"},
            {"adw_id": "def-456", "status": "pending"},
        ]
        result = _resolve_active_runs(store)
        assert "abc-123" in result
        assert "def-456" in result
        assert "2 active run" in result

    def test_graceful_fallback_on_store_exception(self) -> None:
        store = MagicMock()
        store.list_active_runs.side_effect = RuntimeError("DB error")
        result = _resolve_active_runs(store)
        assert "{{ACTIVE_RUNS}}" not in result
        assert result


# ---------------------------------------------------------------------------
# build_system_prompt (integration of all four placeholders)
# ---------------------------------------------------------------------------


FULL_TEMPLATE = textwrap.dedent("""\
    # Orchestrator

    ## Workflows
    {{AVAILABLE_WORKFLOWS}}

    ## Agents
    {{AVAILABLE_AGENTS}}

    ## CLEO
    {{CLEO_CONTEXT}}

    ## Active runs
    {{ACTIVE_RUNS}}
""")

THREE_PLACEHOLDER_TEMPLATE = textwrap.dedent("""\
    # Orchestrator

    ## Workflows
    {{AVAILABLE_WORKFLOWS}}

    ## CLEO
    {{CLEO_CONTEXT}}

    ## Active runs
    {{ACTIVE_RUNS}}
""")


class TestBuildSystemPrompt:
    def test_all_four_placeholders_are_replaced(self, tmp_path: Path) -> None:
        prompt_path = _make_template(tmp_path, FULL_TEMPLATE)
        adws_dir = _make_adws_dir(tmp_path, ["adw_plan_iso", "adw_build_iso"])
        agents_dir = _make_agents_dir(tmp_path, ["implement", "review"])

        mock_cleo = MagicMock(returncode=0, stdout="T074 pending\n")
        store = MagicMock()
        store.list_active_runs.return_value = [{"adw_id": "run-001", "status": "running"}]

        with patch("subprocess.run", return_value=mock_cleo):
            result = build_system_prompt(
                state_store=store,
                prompt_path=prompt_path,
                adws_dir=adws_dir,
                agents_dir=agents_dir,
            )

        assert "{{AVAILABLE_WORKFLOWS}}" not in result
        assert "{{AVAILABLE_AGENTS}}" not in result
        assert "{{CLEO_CONTEXT}}" not in result
        assert "{{ACTIVE_RUNS}}" not in result

    def test_workflows_content_present(self, tmp_path: Path) -> None:
        prompt_path = _make_template(tmp_path, FULL_TEMPLATE)
        adws_dir = _make_adws_dir(tmp_path, ["adw_plan_iso"])
        agents_dir = _make_agents_dir(tmp_path, ["implement"])

        mock_cleo = MagicMock(returncode=0, stdout="cleo output\n")
        store = MagicMock()
        store.list_active_runs.return_value = []

        with patch("subprocess.run", return_value=mock_cleo):
            result = build_system_prompt(
                state_store=store,
                prompt_path=prompt_path,
                adws_dir=adws_dir,
                agents_dir=agents_dir,
            )

        assert "adw_plan_iso" in result

    def test_agents_content_present(self, tmp_path: Path) -> None:
        prompt_path = _make_template(tmp_path, FULL_TEMPLATE)
        adws_dir = _make_adws_dir(tmp_path, ["adw_plan_iso"])
        agents_dir = _make_agents_dir(tmp_path, ["implement", "review", "test"])

        mock_cleo = MagicMock(returncode=0, stdout="cleo output\n")
        store = MagicMock()
        store.list_active_runs.return_value = []

        with patch("subprocess.run", return_value=mock_cleo):
            result = build_system_prompt(
                state_store=store,
                prompt_path=prompt_path,
                adws_dir=adws_dir,
                agents_dir=agents_dir,
            )

        assert "/implement" in result
        assert "/review" in result
        assert "/test" in result

    def test_cleo_fallback_still_replaces_placeholder(self, tmp_path: Path) -> None:
        prompt_path = _make_template(tmp_path, FULL_TEMPLATE)
        adws_dir = _make_adws_dir(tmp_path, ["adw_plan_iso"])
        agents_dir = _make_agents_dir(tmp_path, ["implement"])

        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = build_system_prompt(
                state_store=None,
                prompt_path=prompt_path,
                adws_dir=adws_dir,
                agents_dir=agents_dir,
            )

        assert "{{CLEO_CONTEXT}}" not in result
        assert "{{ACTIVE_RUNS}}" not in result
        assert "{{AVAILABLE_WORKFLOWS}}" not in result
        assert "{{AVAILABLE_AGENTS}}" not in result

    def test_raises_if_template_missing(self, tmp_path: Path) -> None:
        missing = tmp_path / "no_such_file.md"
        with pytest.raises(FileNotFoundError):
            build_system_prompt(prompt_path=missing)

    def test_no_state_store_active_runs_fallback(self, tmp_path: Path) -> None:
        prompt_path = _make_template(tmp_path, FULL_TEMPLATE)
        adws_dir = _make_adws_dir(tmp_path, ["adw_build_iso"])
        agents_dir = _make_agents_dir(tmp_path, ["implement"])

        mock_cleo = MagicMock(returncode=0, stdout="cleo context\n")
        with patch("subprocess.run", return_value=mock_cleo):
            result = build_system_prompt(
                state_store=None,
                prompt_path=prompt_path,
                adws_dir=adws_dir,
                agents_dir=agents_dir,
            )

        assert "{{ACTIVE_RUNS}}" not in result
        assert "StateStore not available" in result

    def test_missing_agents_dir_uses_fallback(self, tmp_path: Path) -> None:
        prompt_path = _make_template(tmp_path, FULL_TEMPLATE)
        adws_dir = _make_adws_dir(tmp_path, ["adw_plan_iso"])
        missing_agents = tmp_path / "no_commands"

        mock_cleo = MagicMock(returncode=0, stdout="cleo context\n")
        store = MagicMock()
        store.list_active_runs.return_value = []

        with patch("subprocess.run", return_value=mock_cleo):
            result = build_system_prompt(
                state_store=store,
                prompt_path=prompt_path,
                adws_dir=adws_dir,
                agents_dir=missing_agents,
            )

        assert "{{AVAILABLE_AGENTS}}" not in result
        assert result  # non-empty

    def test_backward_compatible_without_agents_placeholder(self, tmp_path: Path) -> None:
        """Templates without {{AVAILABLE_AGENTS}} still work correctly."""
        prompt_path = _make_template(tmp_path, THREE_PLACEHOLDER_TEMPLATE)
        adws_dir = _make_adws_dir(tmp_path, ["adw_plan_iso"])
        agents_dir = _make_agents_dir(tmp_path, ["implement"])

        mock_cleo = MagicMock(returncode=0, stdout="T074 pending\n")
        store = MagicMock()
        store.list_active_runs.return_value = []

        with patch("subprocess.run", return_value=mock_cleo):
            result = build_system_prompt(
                state_store=store,
                prompt_path=prompt_path,
                adws_dir=adws_dir,
                agents_dir=agents_dir,
            )

        assert "{{AVAILABLE_WORKFLOWS}}" not in result
        assert "{{CLEO_CONTEXT}}" not in result
        assert "{{ACTIVE_RUNS}}" not in result
        # agents placeholder was not in template so it should not appear
        assert "{{AVAILABLE_AGENTS}}" not in result
