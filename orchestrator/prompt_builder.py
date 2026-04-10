"""Prompt builder for OrchestratorService.

Loads orchestrator_system.md and resolves the dynamic placeholders:

  {{AVAILABLE_WORKFLOWS}}  — adw_* scripts discovered from adws/ directory
  {{AVAILABLE_AGENTS}}     — slash-command templates from .claude/commands/
  {{CLEO_CONTEXT}}         — live task/epic summary via `cleo` CLI subprocess
  {{ACTIVE_RUNS}}          — pending/running ADW runs from StateStore

The module is intentionally free of side-effects at import time; call
:func:`build_system_prompt` to produce the final prompt string.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orchestrator.state_store import StateStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_THIS_DIR = Path(__file__).parent
PROMPT_TEMPLATE_PATH = _THIS_DIR / "system_prompts" / "orchestrator_system.md"

# Relative to tac-master root (two levels up from this file's directory)
_TAC_MASTER_ROOT = _THIS_DIR.parent
ADWS_DIR = _TAC_MASTER_ROOT / "adws"
AGENTS_DIR = _TAC_MASTER_ROOT / ".claude" / "commands"


# ---------------------------------------------------------------------------
# Placeholder resolvers
# ---------------------------------------------------------------------------


def _resolve_available_workflows(adws_dir: Path = ADWS_DIR) -> str:
    """Return a markdown bullet list of adw_* scripts found in *adws_dir*.

    Each entry shows the script stem so the orchestrator can reference workflow
    names exactly as they appear on disk.
    """
    if not adws_dir.exists():
        logger.warning("adws/ directory not found at %s", adws_dir)
        return "_No ADW scripts found — adws/ directory is missing._"

    scripts = sorted(p.stem for p in adws_dir.glob("adw_*.py"))

    if not scripts:
        logger.warning("No adw_*.py scripts found in %s", adws_dir)
        return "_No ADW scripts found in adws/ directory._"

    lines = [f"- `{s}`" for s in scripts]
    logger.debug("Resolved %d ADW workflows", len(scripts))
    return "\n".join(lines)


def _resolve_available_agents(agents_dir: Path = AGENTS_DIR) -> str:
    """Return a markdown bullet list of agent command templates found in *agents_dir*.

    Scans ``*.md`` files directly inside *agents_dir* (not sub-directories) and
    returns their stems as slash-command names so the orchestrator knows which
    in-worktree commands are available to a dispatched ADW agent.
    """
    if not agents_dir.exists():
        logger.warning(".claude/commands/ directory not found at %s", agents_dir)
        return "_No agent command templates found — .claude/commands/ directory is missing._"

    # Only top-level .md files; skip sub-directories like e2e/
    commands = sorted(
        p.stem for p in agents_dir.glob("*.md") if p.is_file()
    )

    if not commands:
        logger.warning("No *.md command templates found in %s", agents_dir)
        return "_No agent command templates found in .claude/commands/ directory._"

    lines = [f"- `/{cmd}`" for cmd in commands]
    logger.debug("Resolved %d agent command templates", len(commands))
    return "\n".join(lines)


def _run_cleo_command(args: list[str], timeout: int = 10) -> str | None:
    """Run a single cleo CLI command and return stdout on success.

    Returns ``None`` on failure (non-zero exit, timeout, or missing binary).
    Callers are responsible for logging at the appropriate level.
    """
    try:
        result = subprocess.run(
            ["cleo"] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        logger.debug(
            "cleo %s returned code %d — skipping section",
            " ".join(args),
            result.returncode,
        )
        return None
    except FileNotFoundError:
        logger.info("cleo CLI not found in PATH")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("cleo %s timed out", " ".join(args))
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("cleo %s failed (%s)", " ".join(args), exc)
        return None


def _resolve_cleo_context() -> str:
    """Return a live CLEO task snapshot by running multiple `cleo` CLI commands.

    Gathers three sections of context, each from a distinct cleo sub-command:

    1. **Project overview** — ``cleo dash`` (summary of all epics/tasks)
    2. **Active task** — ``cleo current`` (task currently in focus)
    3. **Blockers** — ``cleo find "blocked"`` (tasks with blocked status)

    Each section is formatted with a heading so the orchestrator can reference
    it independently.  If the CLI is unavailable or a command fails, its
    section is omitted and a graceful fallback is returned so the prompt
    remains valid.

    The function is called fresh on every ``build_system_prompt()`` invocation
    so the orchestrator always sees current task state.
    """
    sections: list[str] = []

    # ── 1. Project overview ───────────────────────────────────────────────
    dash_output = _run_cleo_command(["dash"])
    if dash_output:
        sections.append("### Project Overview\n\n" + dash_output)
        logger.debug("CLEO_CONTEXT: project overview resolved via 'cleo dash'")

    # ── 2. Active / current task ──────────────────────────────────────────
    current_output = _run_cleo_command(["current"])
    if current_output:
        sections.append("### Active Task\n\n" + current_output)
        logger.debug("CLEO_CONTEXT: active task resolved via 'cleo current'")

    # ── 3. Blockers ───────────────────────────────────────────────────────
    blocked_output = _run_cleo_command(["find", "blocked"])
    if blocked_output:
        sections.append("### Blockers\n\n" + blocked_output)
        logger.debug("CLEO_CONTEXT: blockers resolved via 'cleo find blocked'")

    if sections:
        logger.info(
            "CLEO_CONTEXT resolved with %d section(s) (%d chars)",
            len(sections),
            sum(len(s) for s in sections),
        )
        return "\n\n---\n\n".join(sections)

    return "_CLEO context unavailable — `cleo` CLI could not be reached._"


def _resolve_active_runs(state_store: "StateStore | None") -> str:
    """Return a markdown summary of active runs from *state_store*.

    If *state_store* is ``None`` (e.g. in tests or early init), a graceful
    fallback message is returned.
    """
    if state_store is None:
        return "_StateStore not available — active runs unknown._"

    try:
        runs = state_store.list_active_runs()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to read active runs from StateStore: %s", exc)
        return "_Could not read active runs from StateStore._"

    if not runs:
        return "_No active runs._"

    count = len(runs)
    adw_ids = [r.get("adw_id", "unknown") for r in runs]
    id_list = "\n".join(f"- `{aid}`" for aid in adw_ids)
    logger.debug("Resolved %d active run(s)", count)
    return f"**{count} active run(s):**\n{id_list}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_system_prompt(
    state_store: "StateStore | None" = None,
    *,
    prompt_path: Path = PROMPT_TEMPLATE_PATH,
    adws_dir: Path = ADWS_DIR,
    agents_dir: Path = AGENTS_DIR,
) -> str:
    """Load the orchestrator system prompt template and inject all placeholders.

    Parameters
    ----------
    state_store:
        Live :class:`~orchestrator.state_store.StateStore` instance.  Pass
        ``None`` to skip active-runs injection (graceful fallback is used).
    prompt_path:
        Override the template path (used in tests).
    adws_dir:
        Override the adws/ directory (used in tests).
    agents_dir:
        Override the .claude/commands/ directory (used in tests).

    Returns
    -------
    str
        Fully resolved system prompt with all ``{{PLACEHOLDER}}`` tokens
        replaced.

    Raises
    ------
    FileNotFoundError
        If *prompt_path* does not exist.
    """
    if not prompt_path.exists():
        raise FileNotFoundError(
            f"Orchestrator system prompt template not found: {prompt_path}"
        )

    template = prompt_path.read_text(encoding="utf-8")

    if "{{AVAILABLE_WORKFLOWS}}" in template:
        template = template.replace(
            "{{AVAILABLE_WORKFLOWS}}",
            _resolve_available_workflows(adws_dir),
        )

    if "{{AVAILABLE_AGENTS}}" in template:
        template = template.replace(
            "{{AVAILABLE_AGENTS}}",
            _resolve_available_agents(agents_dir),
        )

    if "{{CLEO_CONTEXT}}" in template:
        template = template.replace(
            "{{CLEO_CONTEXT}}",
            _resolve_cleo_context(),
        )

    if "{{ACTIVE_RUNS}}" in template:
        template = template.replace(
            "{{ACTIVE_RUNS}}",
            _resolve_active_runs(state_store),
        )

    logger.info("System prompt built (%d chars)", len(template))
    return template
