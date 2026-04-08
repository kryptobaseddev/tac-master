"""Token accounting for tac-master.

Closes the budget gap: parses Claude Code JSONL output files produced by
every ADW phase and attributes tokens + cost back to the run, the repo,
and the global daily counters in tac_master.sqlite.

How it works
------------
The tac-7 ADWs write every Claude Code invocation's raw output to:

    <worktree>/agents/<adw_id>/<agent_name>/cc_raw_output.jsonl

Each line is a JSON object from the Claude Code stream. Messages of type
"assistant" carry a `message.usage` block with input/output/cache tokens,
and the final "result" message may carry `total_cost_usd` and an
aggregated `usage` block. We sum these conservatively:

  1. Prefer the final result message's `total_cost_usd` if present.
  2. Otherwise, sum per-assistant-message `usage` fields and price them
     via config/model_prices.yaml, keyed by the `model` field.
  3. Cache writes and reads are priced separately.

The tracker is idempotent per (adw_id, phase_name, file_path):
already-attributed files are skipped via the `token_ledger` table.

Usage
-----
    # In-process, called by dispatcher.reap_finished_runs():
    tracker.attribute_run(adw_id, worktree_path, repo_url)

    # CLI reconciliation:
    uv run orchestrator/token_tracker.py --adw-id abc1d2e3
    uv run orchestrator/token_tracker.py --scan-all        # everything
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------


@dataclass
class ModelPrice:
    input: float
    output: float
    cache_write: float
    cache_read: float


class PriceBook:
    def __init__(self, path: Path):
        raw = yaml.safe_load(path.read_text()) if path.exists() else {}
        self.prices: dict[str, ModelPrice] = {}
        for name, entry in (raw.get("prices") or {}).items():
            self.prices[name] = ModelPrice(**entry)
        if "default" not in self.prices:
            self.prices["default"] = ModelPrice(15.0, 75.0, 18.75, 1.50)

    def lookup(self, model: str) -> ModelPrice:
        """Best-effort fuzzy model lookup.

        The exact model id (e.g. 'claude-opus-4-6-20260317') may not appear
        verbatim — we strip version suffixes and fall back to default.
        """
        if not model:
            return self.prices["default"]
        m = model.lower()
        # Try exact match first
        if m in self.prices:
            return self.prices[m]
        # Strip date suffix (e.g. '-20260317')
        for key in self.prices:
            if m.startswith(key):
                return self.prices[key]
        # Try family match
        for family in ("opus", "sonnet", "haiku"):
            if family in m:
                return self.prices.get(family, self.prices["default"])
        return self.prices["default"]


# ---------------------------------------------------------------------------
# Usage aggregation
# ---------------------------------------------------------------------------


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""

    @property
    def total_tokens(self) -> int:
        return (self.input_tokens + self.output_tokens
                + self.cache_creation_input_tokens
                + self.cache_read_input_tokens)

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_creation_input_tokens += other.cache_creation_input_tokens
        self.cache_read_input_tokens += other.cache_read_input_tokens
        self.cost_usd += other.cost_usd
        if other.model and not self.model:
            self.model = other.model

    def price(self, book: PriceBook) -> float:
        """Compute cost from tokens if cost_usd is zero."""
        if self.cost_usd > 0:
            return self.cost_usd
        p = book.lookup(self.model)
        return (
            self.input_tokens / 1_000_000 * p.input
            + self.output_tokens / 1_000_000 * p.output
            + self.cache_creation_input_tokens / 1_000_000 * p.cache_write
            + self.cache_read_input_tokens / 1_000_000 * p.cache_read
        )


def parse_jsonl_file(path: Path) -> Usage:
    """Extract total Usage from a single cc_raw_output.jsonl file.

    Conservative strategy:
      - Sum every assistant message's `message.usage` block
      - If a `result` message carries `total_cost_usd`, trust it as cost
        (but still track token counts for budgeting)
      - Record the model id from the first assistant message we see
    """
    total = Usage()
    if not path.exists():
        return total

    try:
        with path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                _extract_usage(obj, total)
    except OSError as e:
        log.warning("Could not read %s: %s", path, e)

    return total


def _extract_usage(obj: dict[str, Any], total: Usage) -> None:
    """Pull usage/cost fields from a single Claude Code JSONL record."""
    msg_type = obj.get("type")

    # Claude Code "assistant" messages wrap the Anthropic API response
    if msg_type == "assistant":
        message = obj.get("message") or {}
        usage = message.get("usage") or {}
        model = message.get("model") or ""
        total.add(
            Usage(
                input_tokens=int(usage.get("input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
                cache_creation_input_tokens=int(
                    usage.get("cache_creation_input_tokens") or 0),
                cache_read_input_tokens=int(
                    usage.get("cache_read_input_tokens") or 0),
                model=model,
            )
        )

    # Final "result" message from claude code run (when --output-format=stream-json)
    elif msg_type == "result":
        if isinstance(obj.get("total_cost_usd"), (int, float)):
            total.cost_usd = float(obj["total_cost_usd"])
        usage = obj.get("usage") or {}
        if usage and total.input_tokens == 0:
            total.add(
                Usage(
                    input_tokens=int(usage.get("input_tokens") or 0),
                    output_tokens=int(usage.get("output_tokens") or 0),
                    cache_creation_input_tokens=int(
                        usage.get("cache_creation_input_tokens") or 0),
                    cache_read_input_tokens=int(
                        usage.get("cache_read_input_tokens") or 0),
                )
            )

    # Some Claude Code versions emit a flat top-level "usage" at end of stream
    elif "usage" in obj and isinstance(obj["usage"], dict) and "input_tokens" in obj["usage"]:
        u = obj["usage"]
        total.add(
            Usage(
                input_tokens=int(u.get("input_tokens") or 0),
                output_tokens=int(u.get("output_tokens") or 0),
                cache_creation_input_tokens=int(u.get("cache_creation_input_tokens") or 0),
                cache_read_input_tokens=int(u.get("cache_read_input_tokens") or 0),
            )
        )


# ---------------------------------------------------------------------------
# Attribution
# ---------------------------------------------------------------------------


@dataclass
class PhaseAttribution:
    phase_name: str
    file_path: str
    usage: Usage

    @property
    def tokens(self) -> int:
        return self.usage.total_tokens


class TokenTracker:
    def __init__(self, store, pricebook_path: Path):
        self.store = store
        self.book = PriceBook(pricebook_path)
        self._ensure_ledger()

    def _ensure_ledger(self) -> None:
        """Create the token_ledger table if missing. Idempotent."""
        ddl = """
        CREATE TABLE IF NOT EXISTS token_ledger (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            adw_id         TEXT NOT NULL,
            repo_url       TEXT NOT NULL,
            phase_name     TEXT NOT NULL,
            file_path      TEXT NOT NULL,
            model          TEXT,
            input_tokens   INTEGER NOT NULL DEFAULT 0,
            output_tokens  INTEGER NOT NULL DEFAULT 0,
            cache_write    INTEGER NOT NULL DEFAULT 0,
            cache_read     INTEGER NOT NULL DEFAULT 0,
            cost_usd       REAL NOT NULL DEFAULT 0,
            attributed_at  INTEGER NOT NULL,
            UNIQUE(adw_id, phase_name, file_path)
        );
        CREATE INDEX IF NOT EXISTS idx_ledger_adw ON token_ledger(adw_id);
        CREATE INDEX IF NOT EXISTS idx_ledger_repo ON token_ledger(repo_url);
        """
        with self.store.conn() as c:
            c.executescript(ddl)

    # ------------------------------------------------------------------
    def discover_phase_files(self, worktree_path: Path, adw_id: str) -> list[PhaseAttribution]:
        """Find every cc_raw_output.jsonl under agents/<adw_id>/*/ and parse it."""
        root = worktree_path / "agents" / adw_id
        if not root.exists():
            return []

        out: list[PhaseAttribution] = []
        for agent_dir in sorted(root.iterdir()):
            if not agent_dir.is_dir():
                continue
            for jsonl in agent_dir.glob("**/cc_raw_output.jsonl"):
                usage = parse_jsonl_file(jsonl)
                out.append(PhaseAttribution(
                    phase_name=agent_dir.name,
                    file_path=str(jsonl.resolve()),
                    usage=usage,
                ))
        return out

    # ------------------------------------------------------------------
    def attribute_run(self, adw_id: str, worktree_path: Path, repo_url: str) -> Usage:
        """Scan a finished run and credit the ledger + budget usage tables.

        Idempotent: files already in `token_ledger` with the same
        (adw_id, phase_name, file_path) are skipped.
        """
        import time

        attributions = self.discover_phase_files(worktree_path, adw_id)
        run_total = Usage()
        new_rows = 0

        with self.store.conn() as c:
            for att in attributions:
                # Skip if already attributed
                row = c.execute(
                    "SELECT 1 FROM token_ledger WHERE adw_id=? AND phase_name=? AND file_path=?",
                    (adw_id, att.phase_name, att.file_path),
                ).fetchone()
                if row:
                    continue

                cost = att.usage.price(self.book)

                c.execute(
                    """INSERT INTO token_ledger
                       (adw_id, repo_url, phase_name, file_path, model,
                        input_tokens, output_tokens, cache_write, cache_read,
                        cost_usd, attributed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        adw_id, repo_url, att.phase_name, att.file_path,
                        att.usage.model,
                        att.usage.input_tokens,
                        att.usage.output_tokens,
                        att.usage.cache_creation_input_tokens,
                        att.usage.cache_read_input_tokens,
                        cost,
                        int(time.time()),
                    ),
                )
                run_total.add(att.usage)
                run_total.cost_usd += cost  # accumulate priced cost
                new_rows += 1

        if run_total.total_tokens > 0:
            # Update per-run totals in the runs table
            current = 0
            with self.store.conn() as c:
                row = c.execute(
                    "SELECT tokens_used FROM runs WHERE adw_id=?", (adw_id,)
                ).fetchone()
                if row:
                    current = row["tokens_used"] or 0
                c.execute(
                    "UPDATE runs SET tokens_used=? WHERE adw_id=?",
                    (current + run_total.total_tokens, adw_id),
                )

            # Update rolling daily budget for repo + global
            self.store.add_tokens(repo_url, run_total.total_tokens)

        log.info(
            "Attributed adw=%s repo=%s files=%d tokens=%d cost=$%.4f",
            adw_id, repo_url, new_rows, run_total.total_tokens, run_total.cost_usd,
        )
        return run_total

    # ------------------------------------------------------------------
    def scan_all_active(self) -> int:
        """Re-scan every active/completed run for unattributed files.

        Useful after a daemon restart or backfill. Returns number of
        runs updated.
        """
        with self.store.conn() as c:
            runs = c.execute(
                "SELECT adw_id, repo_url, worktree_path FROM runs"
            ).fetchall()

        updated = 0
        for r in runs:
            wt = r["worktree_path"]
            if not wt:
                continue
            usage = self.attribute_run(r["adw_id"], Path(wt), r["repo_url"])
            if usage.total_tokens > 0:
                updated += 1
        return updated


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from orchestrator.config import load_config
    from orchestrator.state_store import StateStore

    ap = argparse.ArgumentParser(prog="token_tracker")
    ap.add_argument("--adw-id", help="Attribute a single run by id")
    ap.add_argument("--scan-all", action="store_true",
                    help="Re-scan every run in the store")
    ap.add_argument("--home", type=Path, default=None)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = load_config(args.home)
    store = StateStore(cfg.sqlite_path)
    tracker = TokenTracker(store, cfg.home / "config" / "model_prices.yaml")

    if args.scan_all:
        n = tracker.scan_all_active()
        print(f"Updated {n} run(s)")
        return 0

    if args.adw_id:
        with store.conn() as c:
            row = c.execute(
                "SELECT repo_url, worktree_path FROM runs WHERE adw_id=?",
                (args.adw_id,),
            ).fetchone()
        if not row:
            print(f"No run found for adw_id={args.adw_id}", file=sys.stderr)
            return 1
        usage = tracker.attribute_run(args.adw_id, Path(row["worktree_path"]), row["repo_url"])
        print(f"tokens={usage.total_tokens} cost=${usage.cost_usd:.4f}")
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
