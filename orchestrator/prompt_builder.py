"""Prompt builder for OrchestratorService.

Loads orchestrator_system.md and resolves the three dynamic placeholders:

  {{AVAILABLE_WORKFLOWS}}  — adw_* scripts discovered from adws/ directory
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


def _resolve_cleo_context() -> str:
    """Return a live CLEO task snapshot by running the `cleo` CLI.

    Tries ``cleo dash`` first, then ``cleo current`` as a fallback.  If the
    CLI is unavailable or returns an error, a graceful fallback message is
    returned so the prompt remains valid.
    """
    commands = [
        ["cleo", "dash"],
        ["cleo", "current"],
    ]

    for cmd in commands:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                logger.debug("CLEO_CONTEXT resolved via '%s'", " ".join(cmd))
                return result.stdout.strip()
            logger.debug(
                "Command '%s' returned code %d, trying next",
                " ".join(cmd),
                result.returncode,
            )
        except FileNotFoundError:
            logger.info("cleo CLI not found in PATH — using fallback CLEO_CONTEXT")
            break
        except subprocess.TimeoutExpired:
            logger.warning("cleo command timed out — using fallback CLEO_CONTEXT")
            break
        except Exception as exc:  # noqa: BLE001
            logger.warning("cleo command failed (%s) — using fallback CLEO_CONTEXT", exc)
            break

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
