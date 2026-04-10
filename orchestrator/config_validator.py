"""Config schema validator for tac-master.

Checks user YAML files against expected fields and detects common mistakes
such as leftover placeholder values.

Called at daemon startup (via daemon.py) and by:
  uv run orchestrator/daemon.py --doctor

Never overwrites config files — read-only validation only.
Returns warnings as a list of strings; empty list means valid.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Field specifications
# ---------------------------------------------------------------------------

REPOS_REQUIRED_FIELDS: dict[str, type] = {
    "version": int,
    "repos": list,
}

REPOS_ENTRY_REQUIRED: dict[str, type] = {
    "url": str,
}

REPOS_ENTRY_OPTIONAL: dict[str, type] = {
    "self": bool,
    "default_workflow": str,
    "model_set": str,
    "auto_merge": bool,
    "trigger_labels": list,
    "runtime": str,
    "env": dict,
}

BUDGETS_REQUIRED_FIELDS: dict[str, type] = {
    "version": int,
    "global": dict,
}

PLACEHOLDER = "OWNER"


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


def validate_repos_yaml(path: str | Path) -> list[str]:
    """Validate config/repos.yaml. Returns list of warning strings.

    An empty list means the file is structurally valid and contains no
    placeholder values. Non-fatal warnings are prefixed with 'warning:';
    fatal issues (e.g. OWNER placeholders) are prefixed with 'FATAL:'.
    """
    import yaml  # only dependency; already required by the daemon

    warnings: list[str] = []
    p = Path(path)

    try:
        with p.open() as f:
            data: dict[str, Any] = yaml.safe_load(f) or {}
    except Exception as exc:
        return [f"FATAL: cannot parse {p}: {exc}"]

    if not data:
        return [f"FATAL: {p} is empty"]

    for field, expected_type in REPOS_REQUIRED_FIELDS.items():
        if field not in data:
            warnings.append(f"FATAL: repos.yaml missing required field '{field}'")
        elif not isinstance(data[field], expected_type):
            warnings.append(
                f"warning: repos.yaml field '{field}' should be"
                f" {expected_type.__name__}, got {type(data[field]).__name__}"
            )

    repos = data.get("repos")
    if not isinstance(repos, list):
        warnings.append("FATAL: repos.yaml 'repos' must be a list")
        return warnings

    if len(repos) == 0:
        warnings.append("warning: repos.yaml has no repo entries — daemon will idle")

    for i, repo in enumerate(repos):
        if not isinstance(repo, dict):
            warnings.append(f"warning: repos.yaml repos[{i}] is not a mapping")
            continue
        url = repo.get("url", "")
        if not url:
            warnings.append(f"FATAL: repos.yaml repos[{i}] is missing 'url'")
        elif PLACEHOLDER in url:
            warnings.append(
                f"FATAL: repos.yaml repos[{i}].url contains placeholder '{PLACEHOLDER}'"
                f" — replace with your GitHub username"
            )
        # Warn about unknown workflow names
        wf = repo.get("default_workflow", "")
        known_workflows = {
            "patch", "plan_build", "plan_build_test",
            "plan_build_test_review", "sdlc", "sdlc_zte",
        }
        if wf and wf not in known_workflows:
            warnings.append(
                f"warning: repos.yaml repos[{i}].default_workflow '{wf}'"
                f" is not a recognised workflow"
            )

    return warnings


def validate_budgets_yaml(path: str | Path) -> list[str]:
    """Validate config/budgets.yaml. Returns list of warning strings."""
    import yaml

    warnings: list[str] = []
    p = Path(path)

    try:
        with p.open() as f:
            data: dict[str, Any] = yaml.safe_load(f) or {}
    except Exception as exc:
        return [f"FATAL: cannot parse {p}: {exc}"]

    if not data:
        return [f"FATAL: {p} is empty"]

    if "global" not in data:
        warnings.append("FATAL: budgets.yaml missing 'global' section")
    else:
        if "max_tokens_per_day" not in data.get("global", {}):
            warnings.append(
                "FATAL: budgets.yaml global.max_tokens_per_day is required"
            )

    for i, repo in enumerate(data.get("repos") or []):
        if not isinstance(repo, dict):
            continue
        url = repo.get("url", "")
        if PLACEHOLDER in url:
            warnings.append(
                f"FATAL: budgets.yaml repos[{i}].url contains placeholder '{PLACEHOLDER}'"
                f" — replace with your GitHub username"
            )

    return warnings


# ---------------------------------------------------------------------------
# Convenience: validate all standard configs at once
# ---------------------------------------------------------------------------


def validate_all(config_dir: str | Path) -> list[str]:
    """Run all validators. Returns combined warnings from all config files."""
    d = Path(config_dir)
    all_warnings: list[str] = []
    all_warnings.extend(validate_repos_yaml(d / "repos.yaml"))
    all_warnings.extend(validate_budgets_yaml(d / "budgets.yaml"))
    return all_warnings


def has_fatal(warnings: list[str]) -> bool:
    """Return True if any warning is tagged FATAL."""
    return any(w.startswith("FATAL:") for w in warnings)
