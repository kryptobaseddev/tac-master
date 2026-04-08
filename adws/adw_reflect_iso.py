#!/usr/bin/env -S uv run
# /// script
# dependencies = ["python-dotenv", "pydantic"]
# ///

"""ADW Reflect Iso — self-improvement worker.

Runs after every completed ADW run. Reads the adw_state.json and the final
agent outputs under agents/{adw_id}/, synthesizes a short lesson-learned
markdown file, and writes it to $TAC_MASTER_HOME/state/knowledge/.

Lessons are surfaced back to future Lead agents via prompt injection from
the classify_issue / plan slash commands. The knowledge dir is greppable
plain markdown to start; can be upgraded to embeddings later.

Invoked standalone for testing:
    uv run adws/adw_reflect_iso.py <adw-id>

Invoked by the orchestrator after reap_finished_runs detects completion.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Re-use tac-7 primitives
sys.path.insert(0, str(Path(__file__).parent))
from adw_modules.state import ADWState  # noqa: E402
from adw_modules.utils import setup_logger  # noqa: E402


LESSON_TEMPLATE = """\
---
adw_id: {adw_id}
repo: {repo}
issue: {issue_number}
workflow: {workflow}
result: {result}
date: {date}
---

# Lesson: {title}

## Context
- Repository: {repo}
- Issue: #{issue_number}
- Workflow: {workflow}
- Final status: {result}

## What the plan was
{plan_excerpt}

## Phases executed
{phases}

## Artifacts
- Plan file: `{plan_file}`
- Branch: `{branch_name}`
- Worktree: `{worktree_path}`

## Observations
{observations}

## Guidance for future runs
{guidance}
"""


def read_state(adw_id: str) -> dict | None:
    state = ADWState.load(adw_id)
    return state.data if state else None


def read_plan_excerpt(worktree_path: str, plan_file: str | None) -> str:
    if not plan_file:
        return "_(no plan file recorded)_"
    p = Path(worktree_path) / plan_file
    if not p.exists():
        return f"_(plan file not found: {plan_file})_"
    try:
        text = p.read_text()
        # First 30 lines, capped at 2000 chars
        excerpt = "\n".join(text.splitlines()[:30])[:2000]
        return f"```\n{excerpt}\n```"
    except Exception as e:
        return f"_(error reading plan: {e})_"


def synthesize_observations(state: dict) -> tuple[str, str]:
    """Cheap observations and guidance. Can be replaced with an LLM call
    later for richer reflection — for now it's heuristic + deterministic so
    the loop is free and fast.
    """
    all_adws = state.get("all_adws", [])
    phases_run = ", ".join(all_adws) if all_adws else "none"
    issue_class = state.get("issue_class", "unknown")

    obs = [
        f"- Phases completed: {phases_run}",
        f"- Issue classification: {issue_class}",
    ]
    guidance = [
        "- If a similar issue arrives, this workflow succeeded — safe to replay.",
    ]
    if "adw_test_iso" not in all_adws:
        guidance.append("- Tests were skipped; consider running them for similar work.")
    if "adw_review_iso" not in all_adws:
        guidance.append("- Review phase was skipped; visual regressions are possible.")
    return "\n".join(obs), "\n".join(guidance)


def write_lesson(adw_id: str) -> int:
    logger = setup_logger(adw_id, "adw_reflect_iso")
    state = read_state(adw_id)
    if not state:
        logger.error("No state found for adw_id=%s; nothing to reflect on", adw_id)
        return 1

    home = Path(os.getenv("TAC_MASTER_HOME", "."))
    knowledge_dir = home / "state" / "knowledge"
    knowledge_dir.mkdir(parents=True, exist_ok=True)

    issue_number = state.get("issue_number", 0)
    repo = os.getenv("GITHUB_REPO_URL", "unknown")
    workflow = state.get("all_adws", ["unknown"])[0] if state.get("all_adws") else "unknown"
    branch_name = state.get("branch_name", "")
    worktree_path = state.get("worktree_path", "")
    plan_file = state.get("plan_file")
    issue_class = state.get("issue_class", "unknown")
    result = "succeeded" if state.get("plan_file") and state.get("all_adws") else "failed"

    title = branch_name or f"issue-{issue_number}"
    date = time.strftime("%Y-%m-%d")

    obs, guidance = synthesize_observations(state)
    plan_excerpt = read_plan_excerpt(worktree_path, plan_file)

    body = LESSON_TEMPLATE.format(
        adw_id=adw_id,
        repo=repo,
        issue_number=issue_number,
        workflow=workflow,
        result=result,
        date=date,
        title=title,
        plan_excerpt=plan_excerpt,
        phases="\n".join(f"- {p}" for p in state.get("all_adws", [])),
        plan_file=plan_file or "",
        branch_name=branch_name,
        worktree_path=worktree_path,
        observations=obs,
        guidance=guidance,
    )

    slug = title.replace("/", "-").replace(" ", "-")[:60]
    out = knowledge_dir / f"{date}__{slug}__{adw_id}.md"
    out.write_text(body)
    logger.info("Lesson written to %s", out)

    # Also persist to the SQLite knowledge base (FTS5) so the dispatcher
    # can retrieve relevant lessons at future dispatch time.
    try:
        import sys as _sys
        _sys.path.insert(0, str(home))
        from orchestrator.state_store import StateStore  # type: ignore
        from orchestrator.knowledge import KnowledgeBase  # type: ignore

        sqlite_path = home / "state" / "tac_master.sqlite"
        if sqlite_path.exists():
            store = StateStore(sqlite_path)
            kb = KnowledgeBase(store)
            tags = [workflow, result, issue_class]
            kb.upsert(
                adw_id=adw_id,
                repo_url=repo,
                title=title,
                body=body,
                issue_number=int(issue_number) if str(issue_number).isdigit() else None,
                workflow=workflow,
                result=result,
                tags=[t for t in tags if t],
                markdown_path=str(out),
            )
            logger.info("Lesson also indexed in knowledge base")
            store.close()
    except Exception as e:
        # Fall through silently — markdown sidecar is always written
        logger.warning("KB persist failed (non-fatal): %s", e)

    print(str(out))
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: uv run adws/adw_reflect_iso.py <adw-id>", file=sys.stderr)
        sys.exit(1)
    sys.exit(write_lesson(sys.argv[1]))
