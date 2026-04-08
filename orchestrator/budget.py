"""Cost controls / budget enforcer.

Checked before every dispatch. Budget data is accumulated in the state store
by the phase tracker (which reads token counts from Claude Code's JSONL
output captured under agents/{adw_id}/).

The enforcer is conservative: if a repo has zero budget info available, it
uses the defaults from budgets.yaml. If usage > cap, dispatch is refused
and an event is logged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .config import BudgetsConfig, BudgetEntry
from .state_store import StateStore

log = logging.getLogger(__name__)


@dataclass
class BudgetDecision:
    allowed: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.allowed


class BudgetEnforcer:
    def __init__(self, cfg: BudgetsConfig, store: StateStore):
        self.cfg = cfg
        self.store = store

    def can_dispatch(self, repo_url: str) -> BudgetDecision:
        """Check if a new Lead can be dispatched for this repo."""
        # 1. Global concurrent cap
        global_max = int(self.cfg.global_.get("max_concurrent_runs", 15))
        active = self.store.active_runs_count()
        if active >= global_max:
            return BudgetDecision(False, f"global concurrent cap reached ({active}/{global_max})")

        # 2. Per-repo concurrent cap
        entry: BudgetEntry = self.cfg.for_repo(repo_url)
        repo_active = self.store.active_runs_count(repo_url)
        if repo_active >= entry.max_concurrent_runs:
            return BudgetDecision(
                False,
                f"repo concurrent cap reached ({repo_active}/{entry.max_concurrent_runs})",
            )

        # 3. Global daily token cap
        global_tokens, global_runs = self.store.usage_today("__global__")
        global_tok_max = int(self.cfg.global_.get("max_tokens_per_day", 10_000_000))
        global_run_max = int(self.cfg.global_.get("max_runs_per_day", 100))
        if global_tokens >= global_tok_max:
            return BudgetDecision(
                False,
                f"global daily token cap reached ({global_tokens}/{global_tok_max})",
            )
        if global_runs >= global_run_max:
            return BudgetDecision(
                False,
                f"global daily run cap reached ({global_runs}/{global_run_max})",
            )

        # 4. Per-repo daily caps
        repo_tokens, repo_runs = self.store.usage_today(repo_url)
        if repo_tokens >= entry.max_tokens_per_day:
            return BudgetDecision(
                False,
                f"repo daily token cap reached ({repo_tokens}/{entry.max_tokens_per_day})",
            )
        if repo_runs >= entry.max_runs_per_day:
            return BudgetDecision(
                False,
                f"repo daily run cap reached ({repo_runs}/{entry.max_runs_per_day})",
            )

        return BudgetDecision(True)

    def record_dispatch(self, repo_url: str) -> None:
        self.store.add_run_count(repo_url, 1)

    def record_tokens(self, repo_url: str, tokens: int) -> None:
        self.store.add_tokens(repo_url, tokens)

    def warn_if_crossing(self, repo_url: str) -> tuple[bool, str]:
        """Return (crossed, message) if usage crossed the warn threshold."""
        warn_pct = int(self.cfg.alerts.get("warn_at_pct", 75)) / 100.0
        entry = self.cfg.for_repo(repo_url)
        tokens, _ = self.store.usage_today(repo_url)
        if tokens >= entry.max_tokens_per_day * warn_pct:
            pct = int(100 * tokens / max(1, entry.max_tokens_per_day))
            return True, f"{repo_url} at {pct}% of daily token budget"
        return False, ""
