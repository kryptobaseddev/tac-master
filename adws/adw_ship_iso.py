#!/usr/bin/env -S uv run
# /// script
# dependencies = ["python-dotenv", "pydantic"]
# ///

"""
ADW Ship Iso - AI Developer Workflow for shipping (merging) to main

Usage:
  uv run adw_ship_iso.py <issue-number> <adw-id>

Workflow:
1. Load state and validate worktree exists
2. Validate ALL state fields are populated (not None)
3. Merge the open PR via GitHub CLI (gh pr merge):
   - Finds the open PR for the feature branch
   - Merges via gh pr merge --merge --delete-branch
   This avoids touching the local base repo entirely, which prevents
   failures when the base repo has uncommitted changes from prior deploys.
4. Post success message to issue

This workflow REQUIRES that all previous workflows have been run and that
every field in ADWState has a value. This is our final approval step.

Note: Merge is performed via the GitHub API (gh pr merge) rather than
local git operations. This approach is immune to dirty base-repo state.
"""

import sys
import os
import logging
import json
import subprocess
from typing import Optional, Tuple
from dotenv import load_dotenv

from adw_modules.state import ADWState
from adw_modules.github import (
    make_issue_comment,
)
from adw_modules.workflow_ops import format_issue_message
from adw_modules.utils import setup_logger, check_env_vars
from adw_modules.worktree_ops import validate_worktree

# Agent name constant
AGENT_SHIPPER = "shipper"


def merge_pr_via_gh(branch_name: str, logger: logging.Logger) -> Tuple[bool, Optional[str]]:
    """Merge the open PR for branch_name via GitHub CLI (gh pr merge).

    This approach avoids touching the local base repository entirely,
    which prevents failures when the base repo has uncommitted changes
    from prior deploys (e.g. tac-update.sh modifications).

    Args:
        branch_name: The feature branch whose PR should be merged
        logger: Logger instance

    Returns:
        Tuple of (success, error_message)
    """
    github_pat = os.environ.get("GITHUB_PAT", "")
    env = {
        "GH_TOKEN": github_pat,
        "PATH": os.environ.get("PATH", ""),
    }

    # Resolve the repo path from the GITHUB_REPO_URL env var, then
    # fall back to reading git remote from the worktree/clone.
    repo_url = os.environ.get("GITHUB_REPO_URL", "")
    if repo_url:
        repo_path = repo_url.replace("https://github.com/", "").replace(".git", "")
    else:
        # Last-resort: read from git remote in the script's own directory
        try:
            r = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                capture_output=True, text=True,
                cwd=os.path.dirname(os.path.abspath(__file__)),
            )
            repo_path = r.stdout.strip().replace("https://github.com/", "").replace(".git", "")
        except Exception:
            return False, "Could not determine GitHub repo path (no GITHUB_REPO_URL env)"

    logger.info(f"Looking for open PR for branch '{branch_name}' in {repo_path}")

    # Step 1: Find the open PR number for this branch
    try:
        result = subprocess.run(
            [
                "gh", "pr", "list",
                "--repo", repo_path,
                "--head", branch_name,
                "--state", "open",
                "--json", "number,mergeable,title",
            ],
            capture_output=True, text=True, env=env,
        )
        if result.returncode != 0:
            return False, f"gh pr list failed: {result.stderr.strip()}"

        prs = json.loads(result.stdout)
        if not prs:
            return False, (
                f"No open PR found for branch '{branch_name}' in {repo_path}. "
                "Ensure the PR was created before running ship_iso."
            )

        pr = prs[0]
        pr_number = pr["number"]
        mergeable = pr.get("mergeable", "UNKNOWN")
        logger.info(f"Found PR #{pr_number}: '{pr['title']}' (mergeable={mergeable})")

        if mergeable == "CONFLICTING":
            return False, (
                f"PR #{pr_number} has merge conflicts. "
                "Resolve conflicts and re-run the ship workflow."
            )

    except json.JSONDecodeError as exc:
        return False, f"Failed to parse gh pr list output: {exc}"
    except Exception as exc:
        return False, f"Unexpected error finding PR: {exc}"

    # Step 2: Merge via gh pr merge
    logger.info(f"Merging PR #{pr_number} via gh pr merge --merge --delete-branch ...")
    try:
        result = subprocess.run(
            [
                "gh", "pr", "merge", str(pr_number),
                "--repo", repo_path,
                "--merge",
                "--delete-branch",
                "--subject", f"Merge branch '{branch_name}' via ADW Ship workflow",
            ],
            capture_output=True, text=True, env=env,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            # gh prints "Pull request #N was already merged" on duplicate calls;
            # treat that as success so retries are idempotent.
            if "already merged" in stderr.lower():
                logger.info("PR was already merged — treating as success.")
                return True, None
            return False, f"gh pr merge failed: {stderr}"

        logger.info(f"PR #{pr_number} merged successfully via GitHub API.")
        return True, None

    except FileNotFoundError:
        return False, (
            "gh CLI not found. Ensure 'gh' is installed and on PATH. "
            "Cannot fall back to local git merge (dirty-repo guard)."
        )
    except Exception as exc:
        return False, f"Unexpected error during gh pr merge: {exc}"


def validate_state_completeness(state: ADWState, logger: logging.Logger) -> tuple[bool, list[str]]:
    """Validate that all fields in ADWState have values (not None).
    
    Returns:
        tuple of (is_valid, missing_fields)
    """
    # Get the expected fields from ADWStateData model
    expected_fields = {
        "adw_id",
        "issue_number",
        "branch_name",
        "plan_file",
        "issue_class",
        "worktree_path",
        "backend_port",
        "frontend_port",
    }
    
    missing_fields = []
    
    for field in expected_fields:
        value = state.get(field)
        if value is None:
            missing_fields.append(field)
            logger.warning(f"Missing required field: {field}")
        else:
            logger.debug(f"✓ {field}: {value}")
    
    return len(missing_fields) == 0, missing_fields


def main():
    """Main entry point."""
    # Load environment variables
    load_dotenv()
    
    # Parse command line args
    # INTENTIONAL: adw-id is REQUIRED - we need it to find the worktree and state
    if len(sys.argv) < 3:
        print("Usage: uv run adw_ship_iso.py <issue-number> <adw-id>")
        print("\nError: Both issue-number and adw-id are required")
        print("Run the complete SDLC workflow before shipping")
        sys.exit(1)
    
    issue_number = sys.argv[1]
    adw_id = sys.argv[2]
    
    # Try to load existing state
    temp_logger = setup_logger(adw_id, "adw_ship_iso")
    state = ADWState.load(adw_id, temp_logger)
    if not state:
        # No existing state found
        logger = setup_logger(adw_id, "adw_ship_iso")
        logger.error(f"No state found for ADW ID: {adw_id}")
        logger.error("Run the complete SDLC workflow before shipping")
        print(f"\nError: No state found for ADW ID: {adw_id}")
        print("Run the complete SDLC workflow before shipping")
        sys.exit(1)
    
    # Update issue number from state if available
    issue_number = state.get("issue_number", issue_number)
    
    # Track that this ADW workflow has run
    state.append_adw_id("adw_ship_iso")
    
    # Set up logger with ADW ID
    logger = setup_logger(adw_id, "adw_ship_iso")
    logger.info(f"ADW Ship Iso starting - ID: {adw_id}, Issue: {issue_number}")
    
    # Validate environment
    check_env_vars(logger)
    
    # Post initial status
    make_issue_comment(
        issue_number,
        format_issue_message(adw_id, "ops", "🚢 Starting ship workflow\n"
                           "📋 Validating state completeness...")
    )
    
    # Step 1: Validate state completeness
    logger.info("Validating state completeness...")
    is_valid, missing_fields = validate_state_completeness(state, logger)
    
    if not is_valid:
        error_msg = f"State validation failed. Missing fields: {', '.join(missing_fields)}"
        logger.error(error_msg)
        make_issue_comment(
            issue_number,
            format_issue_message(adw_id, AGENT_SHIPPER, f"❌ {error_msg}\n\n"
                               "Please ensure all workflows have been run:\n"
                               "- adw_plan_iso.py (creates plan_file, branch_name, issue_class)\n"
                               "- adw_build_iso.py (implements the plan)\n" 
                               "- adw_test_iso.py (runs tests)\n"
                               "- adw_review_iso.py (reviews implementation)\n"
                               "- adw_document_iso.py (generates docs)")
        )
        sys.exit(1)
    
    logger.info("✅ State validation passed - all fields have values")
    
    # Step 2: Validate worktree exists
    valid, error = validate_worktree(adw_id, state)
    if not valid:
        logger.error(f"Worktree validation failed: {error}")
        make_issue_comment(
            issue_number,
            format_issue_message(adw_id, AGENT_SHIPPER, f"❌ Worktree validation failed: {error}")
        )
        sys.exit(1)
    
    worktree_path = state.get("worktree_path")
    logger.info(f"✅ Worktree validated at: {worktree_path}")
    
    # Step 3: Get branch name
    branch_name = state.get("branch_name")
    logger.info(f"Preparing to merge branch: {branch_name}")
    
    make_issue_comment(
        issue_number,
        format_issue_message(adw_id, AGENT_SHIPPER, f"📋 State validation complete\n"
                           f"🔍 Preparing to merge branch: {branch_name}")
    )
    
    # Step 4: Merge PR via gh CLI (avoids local-repo dirty-state failures)
    logger.info(f"Merging PR for branch {branch_name} via gh pr merge...")
    make_issue_comment(
        issue_number,
        format_issue_message(adw_id, AGENT_SHIPPER, f"🔀 Merging PR for branch `{branch_name}` via GitHub API...\n"
                           "Using `gh pr merge` — immune to dirty base-repo state")
    )

    success, error = merge_pr_via_gh(branch_name, logger)

    if not success:
        logger.error(f"Failed to merge PR: {error}")
        make_issue_comment(
            issue_number,
            format_issue_message(adw_id, AGENT_SHIPPER, f"❌ Failed to merge PR: {error}")
        )
        sys.exit(1)

    logger.info(f"✅ Successfully merged PR for branch {branch_name}")

    # Step 5: Post success message
    make_issue_comment(
        issue_number,
        format_issue_message(adw_id, AGENT_SHIPPER,
                           f"🎉 **Successfully shipped!**\n\n"
                           f"✅ Validated all state fields\n"
                           f"✅ Merged PR for branch `{branch_name}` via GitHub API\n"
                           f"✅ Branch deleted after merge\n\n"
                           f"🚢 Code has been deployed to production!")
    )
    
    # Save final state
    state.save("adw_ship_iso")
    
    # Post final state summary
    make_issue_comment(
        issue_number,
        f"{adw_id}_ops: 📋 Final ship state:\n```json\n{json.dumps(state.data, indent=2)}\n```"
    )
    
    logger.info("Ship workflow completed successfully")


if __name__ == "__main__":
    main()