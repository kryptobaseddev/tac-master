#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "pytest"]
# ///

"""
Regression tests for T009 — classify_issue pipeline fix.

@task T009
@epic T001
@why classify_issue was returning file paths instead of slash commands, blocking PITER dispatch
@what Verifies that (A1) the defensive token extractor rejects file-path responses and
      (B) stale specs/issue-*.md cleanup runs before classify, and
      (C) bare slash tokens are correctly extracted even when noise is present.

Run with:
    cd /mnt/projects/agentic-engineer/tac-master/adws
    pytest adw_tests/test_classify_issue_regression.py -v
"""

import glob
import logging
import os
import sys
import tempfile
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

# Make adw_modules importable whether run from repo root or adws/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from adw_modules.data_types import AgentPromptResponse, GitHubIssue
from adw_modules.workflow_ops import (
    _cleanup_stale_issue_specs,
    _extract_classify_token,
    classify_issue,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

_DUMMY_ISSUE = GitHubIssue(
    number=2,
    title="Web app improvements",
    body="Add mobile UX improvements to the run details page.",
    state="open",
    author={"login": "kryptobaseddev", "id": "1"},
    createdAt=datetime(2026, 1, 1),
    updatedAt=datetime(2026, 1, 1),
    url="https://github.com/kryptobaseddev/tac-master/issues/2",
)

_ADW_ID = "test-adw-001"


def _make_mock_response(output: str, success: bool = True) -> AgentPromptResponse:
    return AgentPromptResponse(output=output, success=success)


# ---------------------------------------------------------------------------
# Fix C — _extract_classify_token unit tests
# ---------------------------------------------------------------------------


class TestExtractClassifyToken:
    """Unit tests for the defensive token extractor."""

    def test_bare_feature_token(self):
        """Bare /feature token is returned unchanged."""
        assert _extract_classify_token("/feature", logger) == "/feature"

    def test_bare_bug_token(self):
        assert _extract_classify_token("/bug", logger) == "/bug"

    def test_bare_chore_token(self):
        assert _extract_classify_token("/chore", logger) == "/chore"

    def test_bare_patch_token(self):
        assert _extract_classify_token("/patch", logger) == "/patch"

    def test_bare_zero_token(self):
        assert _extract_classify_token("0", logger) == "0"

    def test_token_with_trailing_newline(self):
        """Newline-padded output is handled correctly."""
        assert _extract_classify_token("/feature\n", logger) == "/feature"

    def test_file_path_response_returns_raw(self):
        """
        REGRESSION: When the response is a spec file path (the T009 bug), the extractor
        must NOT return a valid token — it returns the stripped raw output.
        This test verifies the extractor correctly fails to find a slash token in a file path.
        """
        file_path = "`specs/issue-2-adw--sdlc_planner-webapp-run-details-mobile-ux.md`"
        result = _extract_classify_token(file_path, logger)
        assert result not in ("/chore", "/bug", "/feature", "/patch"), (
            f"Extractor incorrectly returned a valid token from file path: {result!r}"
        )

    def test_noisy_response_extracts_token(self):
        """When Claude adds explanation around the token, the token is still extracted."""
        noisy = "Based on the issue, I believe this is a feature request.\n/feature"
        assert _extract_classify_token(noisy, logger) == "/feature"

    def test_noisy_response_with_file_path_extracts_token(self):
        """
        If Claude emits both noise AND a valid token in the same response, the token wins.
        This simulates a partially-recovered run that prints context then the command.
        """
        noisy = "Some path: specs/issue-2-foo.md\n/bug"
        assert _extract_classify_token(noisy, logger) == "/bug"

    def test_slash_in_path_not_treated_as_token(self):
        """A /feature substring inside a file path is NOT matched as a standalone token."""
        # The word /feature is not a standalone token when embedded in a longer path
        # e.g. specs/feature-branch/plan.md should not match /feature
        path_with_slash = "specs/feature-branch/plan.md"
        result = _extract_classify_token(path_with_slash, logger)
        # The regex requires the token to not be part of a longer word/path segment
        assert result not in ("/chore", "/bug", "/feature", "/patch"), (
            f"Slash inside path segment incorrectly matched: {result!r}"
        )

    def test_last_token_wins_when_multiple(self):
        """When multiple valid tokens appear, the last one is returned."""
        multi = "/chore\nActually this is /feature"
        assert _extract_classify_token(multi, logger) == "/feature"


# ---------------------------------------------------------------------------
# Fix B — _cleanup_stale_issue_specs unit tests
# ---------------------------------------------------------------------------


class TestCleanupStaleIssueSpecs:
    """Unit tests for the pre-classify stale spec cleanup."""

    def test_removes_stale_spec_files(self):
        """Stale specs/issue-{number}-*.md files are removed before classify."""
        with tempfile.TemporaryDirectory() as tmpdir:
            specs_dir = os.path.join(tmpdir, "specs")
            os.makedirs(specs_dir)
            stale = os.path.join(specs_dir, "issue-2-adw--old-plan.md")
            with open(stale, "w") as f:
                f.write("stale content")

            assert os.path.exists(stale), "Pre-condition: stale file must exist"
            _cleanup_stale_issue_specs(2, tmpdir, logger)
            assert not os.path.exists(stale), "Stale spec must be removed after cleanup"

    def test_idempotent_when_no_stale_files(self):
        """Cleanup does not fail when no stale files exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "specs"))
            # Must not raise
            _cleanup_stale_issue_specs(2, tmpdir, logger)

    def test_does_not_remove_other_issue_specs(self):
        """Cleanup only removes specs for the given issue number, not others."""
        with tempfile.TemporaryDirectory() as tmpdir:
            specs_dir = os.path.join(tmpdir, "specs")
            os.makedirs(specs_dir)
            issue2_spec = os.path.join(specs_dir, "issue-2-adw--plan.md")
            issue3_spec = os.path.join(specs_dir, "issue-3-adw--plan.md")
            for f in (issue2_spec, issue3_spec):
                with open(f, "w") as fh:
                    fh.write("content")

            _cleanup_stale_issue_specs(2, tmpdir, logger)

            assert not os.path.exists(issue2_spec), "issue-2 spec must be removed"
            assert os.path.exists(issue3_spec), "issue-3 spec must be preserved"


# ---------------------------------------------------------------------------
# Integration — classify_issue end-to-end (mocked execute_template)
# ---------------------------------------------------------------------------


class TestClassifyIssueIntegration:
    """Integration tests that mock execute_template to verify the full classify_issue flow."""

    def _run_classify(self, raw_output: str, tmp_cwd: str, success: bool = True):
        mock_response = _make_mock_response(raw_output, success=success)
        with patch("adw_modules.workflow_ops.execute_template", return_value=mock_response):
            return classify_issue(_DUMMY_ISSUE, _ADW_ID, logger, cwd=tmp_cwd)

    def test_valid_bare_feature_response(self, tmp_path):
        """Bare /feature response classifies successfully."""
        cmd, err = self._run_classify("/feature", str(tmp_path))
        assert err is None
        assert cmd == "/feature"

    def test_file_path_response_returns_error(self, tmp_path):
        """
        REGRESSION: The T009 bug scenario.
        classify_issue must return an error (not crash) when Claude returns a spec file path.
        """
        file_path_output = "`specs/issue-2-adw--sdlc_planner-webapp-run-details-mobile-ux.md`"
        cmd, err = self._run_classify(file_path_output, str(tmp_path))
        assert cmd is None, f"Expected None command for file-path response, got: {cmd!r}"
        assert err is not None, "Expected an error message for file-path response"
        assert "Invalid command" in err or "No command" in err

    def test_stale_spec_is_cleaned_before_classify(self, tmp_path):
        """
        REGRESSION (Fix B): Stale specs/issue-*.md files must be removed before classify runs.
        """
        specs_dir = tmp_path / "specs"
        specs_dir.mkdir()
        stale = specs_dir / "issue-2-adw--old-plan.md"
        stale.write_text("stale content from prior failed run")

        assert stale.exists(), "Pre-condition: stale file must exist before classify"

        self._run_classify("/bug", str(tmp_path))

        assert not stale.exists(), (
            "Stale spec file must be removed before classify is invoked"
        )

    def test_noisy_response_with_valid_token_classifies(self, tmp_path):
        """
        Fix C: When Claude adds explanation around the token, classify still succeeds.
        """
        noisy = "The issue describes a new feature request.\n/feature"
        cmd, err = self._run_classify(noisy, str(tmp_path))
        assert err is None
        assert cmd == "/feature"

    def test_failed_response_returns_error(self, tmp_path):
        """A failed execute_template response propagates as an error."""
        cmd, err = self._run_classify("some error", str(tmp_path), success=False)
        assert cmd is None
        assert err == "some error"


# ---------------------------------------------------------------------------
# Entry point for direct execution
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest as _pytest
    raise SystemExit(_pytest.main([__file__, "-v"]))
