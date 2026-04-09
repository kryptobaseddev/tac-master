#!/usr/bin/env python3
"""
Tests for adw_ship_iso.py — specifically the gh pr merge path (T027 fix).

These are unit tests that mock subprocess calls; they do not require
a live GitHub connection.
"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Allow importing from adws/ without an install
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
from adw_ship_iso import merge_pr_via_gh

_log = logging.getLogger("test_ship_iso")


def _make_run(returncode: int, stdout: str = "", stderr: str = "") -> MagicMock:
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


class TestMergePrViaGh(unittest.TestCase):
    """Unit tests for merge_pr_via_gh()."""

    def _logger(self) -> logging.Logger:
        return logging.getLogger("test")

    @patch.dict(os.environ, {
        "GITHUB_PAT": "ghp_test",
        "GITHUB_REPO_URL": "https://github.com/owner/repo",
    })
    @patch("adw_ship_iso.subprocess.run")
    def test_success(self, mock_run: MagicMock) -> None:
        """Happy path: open mergeable PR found, merge succeeds."""
        list_resp = json.dumps([{"number": 42, "mergeable": "MERGEABLE", "title": "feat: thing"}])
        mock_run.side_effect = [
            _make_run(0, stdout=list_resp),  # gh pr list
            _make_run(0),                    # gh pr merge
        ]
        ok, err = merge_pr_via_gh("feat/my-branch", self._logger())
        self.assertTrue(ok)
        self.assertIsNone(err)

    @patch.dict(os.environ, {
        "GITHUB_PAT": "ghp_test",
        "GITHUB_REPO_URL": "https://github.com/owner/repo",
    })
    @patch("adw_ship_iso.subprocess.run")
    def test_no_open_pr(self, mock_run: MagicMock) -> None:
        """No open PR for branch → returns False with descriptive error."""
        mock_run.return_value = _make_run(0, stdout="[]")
        ok, err = merge_pr_via_gh("feat/no-pr", self._logger())
        self.assertFalse(ok)
        self.assertIn("No open PR found", err)

    @patch.dict(os.environ, {
        "GITHUB_PAT": "ghp_test",
        "GITHUB_REPO_URL": "https://github.com/owner/repo",
    })
    @patch("adw_ship_iso.subprocess.run")
    def test_conflicting_pr(self, mock_run: MagicMock) -> None:
        """PR has merge conflicts → returns False without attempting merge."""
        list_resp = json.dumps([{"number": 7, "mergeable": "CONFLICTING", "title": "oops"}])
        mock_run.return_value = _make_run(0, stdout=list_resp)
        ok, err = merge_pr_via_gh("feat/conflicts", self._logger())
        self.assertFalse(ok)
        self.assertIn("merge conflicts", err)
        # gh pr merge should NOT have been called
        self.assertEqual(mock_run.call_count, 1)

    @patch.dict(os.environ, {
        "GITHUB_PAT": "ghp_test",
        "GITHUB_REPO_URL": "https://github.com/owner/repo",
    })
    @patch("adw_ship_iso.subprocess.run")
    def test_already_merged_is_idempotent(self, mock_run: MagicMock) -> None:
        """gh pr merge returns 'already merged' → treated as success."""
        list_resp = json.dumps([{"number": 3, "mergeable": "MERGEABLE", "title": "fix"}])
        mock_run.side_effect = [
            _make_run(0, stdout=list_resp),
            _make_run(1, stderr="Pull request #3 was already merged"),
        ]
        ok, err = merge_pr_via_gh("feat/already", self._logger())
        self.assertTrue(ok)
        self.assertIsNone(err)

    @patch.dict(os.environ, {
        "GITHUB_PAT": "ghp_test",
        "GITHUB_REPO_URL": "https://github.com/owner/repo",
    })
    @patch("adw_ship_iso.subprocess.run")
    def test_gh_list_failure(self, mock_run: MagicMock) -> None:
        """gh pr list exits non-zero → returns False."""
        mock_run.return_value = _make_run(1, stderr="unauthorized")
        ok, err = merge_pr_via_gh("feat/x", self._logger())
        self.assertFalse(ok)
        self.assertIn("gh pr list failed", err)

    @patch.dict(os.environ, {
        "GITHUB_PAT": "ghp_test",
        "GITHUB_REPO_URL": "https://github.com/owner/repo",
    })
    @patch("adw_ship_iso.subprocess.run")
    def test_dirty_base_repo_not_touched(self, mock_run: MagicMock) -> None:
        """merge_pr_via_gh must never call git checkout or git pull."""
        list_resp = json.dumps([{"number": 9, "mergeable": "MERGEABLE", "title": "ship"}])
        mock_run.side_effect = [
            _make_run(0, stdout=list_resp),
            _make_run(0),
        ]
        merge_pr_via_gh("feat/ship", self._logger())
        for call in mock_run.call_args_list:
            cmd = call[0][0] if call[0] else call[1].get("args", [])
            cmd_str = " ".join(str(c) for c in cmd)
            self.assertNotIn("git checkout", cmd_str,
                             "merge_pr_via_gh must not call git checkout")
            self.assertNotIn("git pull", cmd_str,
                             "merge_pr_via_gh must not call git pull")


if __name__ == "__main__":
    unittest.main()
