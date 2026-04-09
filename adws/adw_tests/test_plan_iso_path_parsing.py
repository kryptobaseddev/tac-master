#!/usr/bin/env python3
"""
Unit tests for plan_file_path backtick-stripping logic in adw_plan_iso.py.

Covers the fix for T016: sdlc_planner sometimes returns paths wrapped in
backticks (e.g. `specs/issue-N-title.md`), which broke os.path.exists.
"""

import unittest


def parse_plan_file_path(raw_output: str) -> str:
    """
    Mirror the extraction logic from adw_plan_iso.py line 255:

        plan_file_path = plan_response.output.strip().strip("`").strip()
    """
    return raw_output.strip().strip("`").strip()


class TestPlanFilePathParsing(unittest.TestCase):
    """Verify plan_file_path is correctly cleaned before os.path.exists."""

    def test_plain_path_unchanged(self):
        """A plain path should pass through unchanged."""
        self.assertEqual(
            parse_plan_file_path("specs/issue-42-add-feature.md"),
            "specs/issue-42-add-feature.md",
        )

    def test_backtick_wrapped_path(self):
        """Backtick-wrapped path (the observed sdlc_planner failure mode) must be stripped."""
        self.assertEqual(
            parse_plan_file_path("`specs/issue-42-add-feature.md`"),
            "specs/issue-42-add-feature.md",
        )

    def test_backtick_with_surrounding_whitespace(self):
        """Whitespace outside and inside backticks should all be removed."""
        self.assertEqual(
            parse_plan_file_path("  `specs/issue-7-fix-auth.md`  "),
            "specs/issue-7-fix-auth.md",
        )

    def test_leading_trailing_whitespace_only(self):
        """Whitespace-only path with no backticks normalises to empty string."""
        self.assertEqual(parse_plan_file_path("   "), "")

    def test_empty_string(self):
        """Empty string stays empty."""
        self.assertEqual(parse_plan_file_path(""), "")

    def test_path_with_internal_spaces(self):
        """Internal spaces in the path are preserved."""
        self.assertEqual(
            parse_plan_file_path("`specs/issue 10 my plan.md`"),
            "specs/issue 10 my plan.md",
        )

    def test_single_backtick_prefix_only(self):
        """A single leading backtick (malformed) is still removed."""
        self.assertEqual(
            parse_plan_file_path("`specs/issue-1.md"),
            "specs/issue-1.md",
        )

    def test_single_backtick_suffix_only(self):
        """A single trailing backtick (malformed) is still removed."""
        self.assertEqual(
            parse_plan_file_path("specs/issue-1.md`"),
            "specs/issue-1.md",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
