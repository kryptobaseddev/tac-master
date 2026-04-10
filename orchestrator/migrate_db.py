"""migrate_db.py — Apply v2 orchestrator schema to tac_master.sqlite.

Usage:
    uv run orchestrator/migrate_db.py [path/to/tac_master.sqlite]

If no path is given, defaults to the path configured via tac-master's
config (tac-master.yaml → state_path), falling back to
orchestrator/state/tac_master.sqlite.

The migration is idempotent — safe to run on an existing database or a
fresh one. Existing tables (repos, issues, runs, phases, events,
budget_usage) are never altered or dropped.
"""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path


def _default_db_path() -> Path:
    """Resolve database path from config or fall back to a default location."""
    here = Path(__file__).parent
    candidates = [
        here / "state" / "tac_master.sqlite",
        here.parent / "state" / "tac_master.sqlite",
        here / "state.db",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    # If nothing exists yet, use the first candidate (will be created).
    return candidates[0]


def migrate(db_path: Path) -> None:
    print(f"[migrate_db] target: {db_path}")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")

        # Tables present before migration
        before = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        print(f"[migrate_db] tables before: {sorted(before)}")

        # Load and execute the v2 schema
        schema_path = Path(__file__).parent / "schema" / "v2_orchestrator.sql"
        sql = schema_path.read_text()
        conn.executescript(sql)
        conn.commit()

        # Tables present after migration
        after = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        new_tables = after - before
        print(f"[migrate_db] new tables added: {sorted(new_tables)}")

        # Confirm schema_version entry
        row = conn.execute(
            "SELECT version, description, applied_at FROM schema_version WHERE version=2"
        ).fetchone()
        if row:
            print(
                f"[migrate_db] schema_version: v{row['version']} — {row['description']}"
                f" (applied_at={row['applied_at']})"
            )

        print("[migrate_db] migration complete (idempotent, no data loss).")
    finally:
        conn.close()


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else _default_db_path()
    migrate(path)
