#!/usr/bin/env python3
"""
Tests for T011 / T003 — Dashboard event attribution env passthrough.

Verifies that get_safe_subprocess_env() forwards ADW_ID and GITHUB_REPO_URL
into the subprocess environment dict so that send_event.py hooks can populate
dashboard events with non-NULL attribution without depending on fragile
filesystem heuristics or git shellout.

Also verifies that prompt_claude_code() injects ADW_ID from request.adw_id
directly, which is the belt-and-suspenders injection site.

@task T011
@epic T003
@why Hook events were arriving with NULL adw_id/repo_url because the env
      allowlist stripped those vars before reaching the claude subprocess tree
@what Adds ADW_ID and GITHUB_REPO_URL to the allowlist in get_safe_subprocess_env
      and injects ADW_ID directly from request.adw_id in prompt_claude_code
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# Make adw_modules importable when run from the repo root or adws/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from adw_modules.utils import get_safe_subprocess_env


# ---------------------------------------------------------------------------
# Tests: get_safe_subprocess_env() env-var path
# ---------------------------------------------------------------------------

class TestGetSafeSubprocessEnvAttribution(unittest.TestCase):
    """Verify attribution vars flow through the subprocess env allowlist."""

    def test_adw_id_included_when_set(self):
        """ADW_ID in parent env MUST appear in the returned dict."""
        with patch.dict(os.environ, {"ADW_ID": "test-adw-id", "ANTHROPIC_API_KEY": "dummy"}, clear=False):
            result = get_safe_subprocess_env()
        self.assertIn("ADW_ID", result, "ADW_ID must be present in subprocess env")
        self.assertEqual(result["ADW_ID"], "test-adw-id")

    def test_github_repo_url_included_when_set(self):
        """GITHUB_REPO_URL in parent env MUST appear in the returned dict."""
        with patch.dict(os.environ, {"GITHUB_REPO_URL": "https://github.com/foo/bar", "ANTHROPIC_API_KEY": "dummy"}, clear=False):
            result = get_safe_subprocess_env()
        self.assertIn("GITHUB_REPO_URL", result, "GITHUB_REPO_URL must be present in subprocess env")
        self.assertEqual(result["GITHUB_REPO_URL"], "https://github.com/foo/bar")

    def test_both_attribution_vars_present_together(self):
        """Both ADW_ID and GITHUB_REPO_URL must coexist in the result."""
        fake_env = {
            "ADW_ID": "abc12345",
            "GITHUB_REPO_URL": "https://github.com/kryptobaseddev/tac-master",
            "ANTHROPIC_API_KEY": "dummy",
        }
        with patch.dict(os.environ, fake_env, clear=False):
            result = get_safe_subprocess_env()
        self.assertEqual(result["ADW_ID"], "abc12345")
        self.assertEqual(result["GITHUB_REPO_URL"], "https://github.com/kryptobaseddev/tac-master")

    def test_adw_id_absent_when_not_set(self):
        """ADW_ID must NOT appear in the result when the parent env lacks it."""
        env_without = {k: v for k, v in os.environ.items() if k != "ADW_ID"}
        env_without["ANTHROPIC_API_KEY"] = "dummy"
        with patch.dict(os.environ, env_without, clear=True):
            result = get_safe_subprocess_env()
        self.assertNotIn("ADW_ID", result)

    def test_github_repo_url_absent_when_not_set(self):
        """GITHUB_REPO_URL must NOT appear when the parent env lacks it."""
        env_without = {k: v for k, v in os.environ.items() if k != "GITHUB_REPO_URL"}
        env_without["ANTHROPIC_API_KEY"] = "dummy"
        with patch.dict(os.environ, env_without, clear=True):
            result = get_safe_subprocess_env()
        self.assertNotIn("GITHUB_REPO_URL", result)

    def test_none_values_filtered(self):
        """No key in the result dict should map to None."""
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "dummy"}, clear=False):
            result = get_safe_subprocess_env()
        for key, value in result.items():
            self.assertIsNotNone(value, f"Key {key!r} has None value — should be filtered")


# ---------------------------------------------------------------------------
# Tests: prompt_claude_code() direct injection of ADW_ID from request
# ---------------------------------------------------------------------------

class TestPromptClaudeCodeAttributionInjection(unittest.TestCase):
    """Verify prompt_claude_code() injects ADW_ID from request.adw_id."""

    def _make_request(self, adw_id: str = "deadbeef"):
        from adw_modules.data_types import AgentPromptRequest
        return AgentPromptRequest(
            prompt="echo test",
            adw_id=adw_id,
            agent_name="ops",
            model="sonnet",
            dangerously_skip_permissions=False,
            output_file="/tmp/test_output.jsonl",
            working_dir=None,
        )

    def test_adw_id_injected_from_request(self):
        """After get_claude_env(), prompt_claude_code must add ADW_ID from request."""
        # We patch subprocess.run to capture the env dict it receives.
        captured_envs = []

        def fake_run(*args, **kwargs):
            captured_envs.append(kwargs.get("env", {}))
            # Return a mock result with returncode != 0 to short-circuit parsing.
            m = MagicMock()
            m.returncode = 1
            return m

        from adw_modules import agent as agent_mod

        with patch.object(agent_mod, "check_claude_installed", return_value=None), \
             patch.object(agent_mod, "save_prompt"), \
             patch("os.makedirs"), \
             patch("builtins.open", unittest.mock.mock_open()), \
             patch("subprocess.run", side_effect=fake_run):
            from adw_modules.agent import prompt_claude_code
            request = self._make_request(adw_id="cafebabe")
            # This will "fail" (returncode=1) but we only care about the captured env.
            prompt_claude_code(request)

        self.assertTrue(captured_envs, "subprocess.run was never called")
        env_passed = captured_envs[0]
        self.assertIn("ADW_ID", env_passed, "ADW_ID must be in the subprocess env dict")
        self.assertEqual(env_passed["ADW_ID"], "cafebabe")


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
