"""Config loader for tac-master.

Loads and validates:
  - config/repos.yaml      (allowlist)
  - config/budgets.yaml    (cost controls)
  - config/policies.yaml   (execution policies)
  - config/identity.env    (secrets)

Fails loudly on missing files or schema drift.
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses — validated shape of each config file
# ---------------------------------------------------------------------------


@dataclass
class RepoConfig:
    url: str
    self: bool = False
    default_workflow: str = "sdlc"
    model_set: str = "base"
    poll_interval: int = 20
    triggers: list[str] = field(default_factory=lambda: ["new_issue", "comment_adw"])
    trigger_labels: list[str] = field(default_factory=list)
    auto_merge: bool = False
    dry_run: bool = False
    env: dict[str, str] = field(default_factory=dict)
    # Runtime mode: "native" (default, fast) or "podman" (isolated, per-repo image)
    runtime: str = "native"
    # Container image to use when runtime == "podman". Defaults to tac-worker:latest.
    container_image: str = "tac-worker:latest"

    @property
    def slug(self) -> str:
        """Extract owner/repo from url for filesystem paths."""
        # https://github.com/OWNER/REPO -> OWNER/REPO
        parts = self.url.rstrip("/").replace(".git", "").split("/")
        return f"{parts[-2]}/{parts[-1]}"

    @property
    def fs_slug(self) -> str:
        """owner_repo (filesystem-safe)."""
        return self.slug.replace("/", "_")


@dataclass
class ReposConfig:
    version: int
    defaults: dict[str, Any]
    repos: list[RepoConfig]

    def find(self, url: str) -> RepoConfig | None:
        for r in self.repos:
            if r.url.rstrip("/") == url.rstrip("/"):
                return r
        return None


@dataclass
class BudgetEntry:
    max_tokens_per_day: int = 2000000
    max_runs_per_day: int = 20
    max_concurrent_runs: int = 5
    max_tokens_per_run: int = 500000


@dataclass
class BudgetsConfig:
    version: int
    global_: dict[str, Any]
    defaults: BudgetEntry
    repos: dict[str, BudgetEntry]  # keyed by url
    alerts: dict[str, Any]

    def for_repo(self, url: str) -> BudgetEntry:
        return self.repos.get(url.rstrip("/"), self.defaults)


@dataclass
class PoliciesConfig:
    version: int
    safety: dict[str, Any]
    workflows: dict[str, dict[str, Any]]
    self_improvement: dict[str, Any]


@dataclass
class TacMasterConfig:
    home: Path
    repos: ReposConfig
    budgets: BudgetsConfig
    policies: PoliciesConfig
    identity: dict[str, str]  # env-loaded secrets

    @property
    def state_dir(self) -> Path:
        return self.home / "state"

    @property
    def repos_dir(self) -> Path:
        return self.home / "repos"

    @property
    def trees_dir(self) -> Path:
        return self.home / "trees"

    @property
    def logs_dir(self) -> Path:
        return self.home / "logs"

    @property
    def knowledge_dir(self) -> Path:
        return self.state_dir / "knowledge"

    @property
    def sqlite_path(self) -> Path:
        return self.state_dir / "tac_master.sqlite"


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(
            f"Missing config file: {path}\n"
            f"Copy {path.with_suffix(path.suffix + '.sample')} to {path} and customize."
        )
    with path.open() as f:
        return yaml.safe_load(f) or {}


def load_repos(path: Path) -> ReposConfig:
    raw = _load_yaml(path)
    defaults = raw.get("defaults", {})
    repos = []
    for entry in raw.get("repos", []):
        # Merge defaults into each repo entry (repo values win)
        merged = {**defaults, **entry}
        # Remove keys not in RepoConfig
        merged.pop("max_tokens_per_day", None)  # belongs to budgets
        repos.append(RepoConfig(**{k: v for k, v in merged.items() if k in RepoConfig.__annotations__}))
    return ReposConfig(version=raw.get("version", 1), defaults=defaults, repos=repos)


def load_budgets(path: Path) -> BudgetsConfig:
    raw = _load_yaml(path)
    defaults_raw = raw.get("defaults", {})
    defaults = BudgetEntry(**{k: v for k, v in defaults_raw.items() if k in BudgetEntry.__annotations__})
    repos: dict[str, BudgetEntry] = {}
    for entry in raw.get("repos") or []:
        url = entry.pop("url").rstrip("/")
        merged = {**defaults_raw, **entry}
        repos[url] = BudgetEntry(
            **{k: v for k, v in merged.items() if k in BudgetEntry.__annotations__}
        )
    return BudgetsConfig(
        version=raw.get("version", 1),
        global_=raw.get("global", {}),
        defaults=defaults,
        repos=repos,
        alerts=raw.get("alerts", {}),
    )


def load_policies(path: Path) -> PoliciesConfig:
    raw = _load_yaml(path)
    return PoliciesConfig(
        version=raw.get("version", 1),
        safety=raw.get("safety", {}),
        workflows=raw.get("workflows", {}),
        self_improvement=raw.get("self_improvement", {}),
    )


def load_identity(path: Path) -> dict[str, str]:
    if path.exists():
        load_dotenv(path, override=False)
    # Also try top-level .env for dev convenience
    top_env = path.parent.parent / ".env"
    if top_env.exists():
        load_dotenv(top_env, override=False)

    required = ["GITHUB_USER", "GITHUB_PAT", "ANTHROPIC_API_KEY"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise RuntimeError(
            f"Missing required env vars: {missing}. "
            f"Set them in {path} or export in shell."
        )
    return {k: os.getenv(k, "") for k in required + [
        "GITHUB_EMAIL", "GITHUB_WEBHOOK_SECRET", "CLAUDE_CODE_PATH",
        "TAC_MASTER_HOME", "TAC_MASTER_LOG_LEVEL",
        "TAC_MASTER_POLL_INTERVAL", "TAC_MASTER_MAX_CONCURRENT",
    ]}


def load_config(home: Path | str | None = None) -> TacMasterConfig:
    """Top-level config loader.

    `home` defaults to TAC_MASTER_HOME env var, else cwd.
    """
    if home is None:
        home = Path(os.getenv("TAC_MASTER_HOME", "."))
    home = Path(home).resolve()

    cfg_dir = home / "config"
    identity = load_identity(cfg_dir / "identity.env")
    repos = load_repos(cfg_dir / "repos.yaml")
    budgets = load_budgets(cfg_dir / "budgets.yaml")
    policies = load_policies(cfg_dir / "policies.yaml")

    # Ensure runtime dirs exist
    for d in [home / "state" / "knowledge", home / "repos", home / "trees", home / "logs"]:
        d.mkdir(parents=True, exist_ok=True)

    return TacMasterConfig(
        home=home,
        repos=repos,
        budgets=budgets,
        policies=policies,
        identity=identity,
    )
