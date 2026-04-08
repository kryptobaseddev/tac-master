"""Knowledge base for tac-master.

SQLite FTS5-backed lesson store. Every completed ADW run contributes a
lesson (via adw_reflect_iso.py) which gets written both to a markdown
file in state/knowledge/ (for human review + git) and to the FTS5 index
in tac_master.sqlite (for machine retrieval at dispatch time).

Retrieval: `fetch_relevant(issue_title, repo_url, k)` returns the top-K
lessons by BM25 relevance, filtered to the same repo first and falling
back to cross-repo results if fewer than K matches exist.

Injection: the dispatcher (or a future hook in classify_issue.md) can
prepend the relevant lessons to the Lead's prompt via a prompt-tail file
written into the worktree before claude code boots.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

log = logging.getLogger(__name__)


KNOWLEDGE_SCHEMA = """
-- Primary lessons table
CREATE TABLE IF NOT EXISTS lessons (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    adw_id          TEXT NOT NULL,
    repo_url        TEXT NOT NULL,
    issue_number    INTEGER,
    title           TEXT NOT NULL,
    workflow        TEXT,
    result          TEXT,                -- succeeded | failed
    tags            TEXT,                -- comma-separated
    body            TEXT NOT NULL,       -- markdown body
    markdown_path   TEXT,                -- on-disk sidecar path
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL,
    UNIQUE(adw_id)
);
CREATE INDEX IF NOT EXISTS idx_lessons_repo ON lessons(repo_url);
CREATE INDEX IF NOT EXISTS idx_lessons_result ON lessons(result);
CREATE INDEX IF NOT EXISTS idx_lessons_created ON lessons(created_at);

-- FTS5 virtual table over lesson body + title
CREATE VIRTUAL TABLE IF NOT EXISTS lessons_fts USING fts5(
    title,
    body,
    tags,
    repo_url UNINDEXED,
    adw_id UNINDEXED,
    content='lessons',
    content_rowid='id',
    tokenize='porter unicode61'
);

-- Triggers to keep FTS in sync
CREATE TRIGGER IF NOT EXISTS lessons_ai AFTER INSERT ON lessons BEGIN
    INSERT INTO lessons_fts(rowid, title, body, tags, repo_url, adw_id)
    VALUES (new.id, new.title, new.body, new.tags, new.repo_url, new.adw_id);
END;
CREATE TRIGGER IF NOT EXISTS lessons_ad AFTER DELETE ON lessons BEGIN
    INSERT INTO lessons_fts(lessons_fts, rowid, title, body, tags, repo_url, adw_id)
    VALUES ('delete', old.id, old.title, old.body, old.tags, old.repo_url, old.adw_id);
END;
CREATE TRIGGER IF NOT EXISTS lessons_au AFTER UPDATE ON lessons BEGIN
    INSERT INTO lessons_fts(lessons_fts, rowid, title, body, tags, repo_url, adw_id)
    VALUES ('delete', old.id, old.title, old.body, old.tags, old.repo_url, old.adw_id);
    INSERT INTO lessons_fts(rowid, title, body, tags, repo_url, adw_id)
    VALUES (new.id, new.title, new.body, new.tags, new.repo_url, new.adw_id);
END;
"""


@dataclass
class Lesson:
    id: int | None
    adw_id: str
    repo_url: str
    issue_number: int | None
    title: str
    workflow: str | None
    result: str | None
    tags: str | None
    body: str
    markdown_path: str | None
    created_at: int
    updated_at: int


class KnowledgeBase:
    def __init__(self, store):
        self.store = store
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self.store.conn() as c:
            c.executescript(KNOWLEDGE_SCHEMA)

    # ------------------------------------------------------------------
    def upsert(
        self,
        adw_id: str,
        repo_url: str,
        title: str,
        body: str,
        *,
        issue_number: int | None = None,
        workflow: str | None = None,
        result: str | None = None,
        tags: Iterable[str] = (),
        markdown_path: str | None = None,
    ) -> int:
        now = int(time.time())
        tag_str = ",".join(sorted(set(tags))) if tags else None
        with self.store.conn() as c:
            existing = c.execute(
                "SELECT id FROM lessons WHERE adw_id=?", (adw_id,)
            ).fetchone()
            if existing:
                c.execute(
                    """UPDATE lessons SET
                         repo_url=?, issue_number=?, title=?, workflow=?,
                         result=?, tags=?, body=?, markdown_path=?, updated_at=?
                       WHERE adw_id=?""",
                    (repo_url, issue_number, title, workflow, result,
                     tag_str, body, markdown_path, now, adw_id),
                )
                return existing["id"]
            cur = c.execute(
                """INSERT INTO lessons
                     (adw_id, repo_url, issue_number, title, workflow, result,
                      tags, body, markdown_path, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (adw_id, repo_url, issue_number, title, workflow, result,
                 tag_str, body, markdown_path, now, now),
            )
            return cur.lastrowid or 0

    # ------------------------------------------------------------------
    def search(self, query: str, *, repo_url: str | None = None,
               limit: int = 5) -> list[Lesson]:
        """Full-text search with BM25 ranking, optionally repo-scoped."""
        q = _sanitize_fts(query)
        if not q:
            return []
        params: list = [q]
        where_extra = ""
        if repo_url:
            where_extra = " AND l.repo_url = ?"
            params.append(repo_url)
        params.append(limit)

        sql = f"""
        SELECT l.*
        FROM lessons_fts
        JOIN lessons l ON l.id = lessons_fts.rowid
        WHERE lessons_fts MATCH ? {where_extra}
        ORDER BY bm25(lessons_fts) ASC
        LIMIT ?
        """
        with self.store.conn() as c:
            rows = c.execute(sql, params).fetchall()
        return [self._row_to_lesson(r) for r in rows]

    def fetch_relevant(self, issue_title: str, repo_url: str,
                       k: int = 3) -> list[Lesson]:
        """Combined same-repo + cross-repo retrieval."""
        primary = self.search(issue_title, repo_url=repo_url, limit=k)
        if len(primary) >= k:
            return primary
        remaining = k - len(primary)
        have_ids = {l.adw_id for l in primary}
        fallback = [
            l for l in self.search(issue_title, limit=k + remaining)
            if l.adw_id not in have_ids
        ][:remaining]
        return primary + fallback

    # ------------------------------------------------------------------
    def recent(self, limit: int = 20, repo_url: str | None = None) -> list[Lesson]:
        with self.store.conn() as c:
            if repo_url:
                rows = c.execute(
                    "SELECT * FROM lessons WHERE repo_url=? ORDER BY created_at DESC LIMIT ?",
                    (repo_url, limit),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM lessons ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [self._row_to_lesson(r) for r in rows]

    def count(self) -> int:
        with self.store.conn() as c:
            r = c.execute("SELECT COUNT(*) AS n FROM lessons").fetchone()
            return r["n"] if r else 0

    # ------------------------------------------------------------------
    def render_prompt_context(self, lessons: list[Lesson]) -> str:
        """Format lessons for prompt injection into Claude Code."""
        if not lessons:
            return ""
        parts = [
            "## Prior lessons from similar work",
            "",
            "These are relevant reflections from previous completed runs. "
            "Use them to inform your approach, especially for classification "
            "and plan generation. Do NOT blindly copy; adapt to the current issue.",
            "",
        ]
        for i, l in enumerate(lessons, 1):
            short_body = l.body[:1500]
            parts.append(f"### {i}. {l.title} ({l.result or '?'})")
            parts.append(f"*repo: {l.repo_url}  •  adw: {l.adw_id}*")
            parts.append("")
            parts.append(short_body)
            parts.append("")
        return "\n".join(parts)

    def write_prompt_tail(self, worktree_path: Path, lessons: list[Lesson]) -> Path | None:
        """Write a prompt-tail file the Lead can prepend to its planner prompt.

        Returns the path or None if no lessons to write. The file lives
        under the worktree so Claude Code can discover it.
        """
        if not lessons:
            return None
        out = worktree_path / "agents" / "_knowledge" / "prompt_tail.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(self.render_prompt_context(lessons))
        return out

    # ------------------------------------------------------------------
    def _row_to_lesson(self, row) -> Lesson:
        return Lesson(
            id=row["id"],
            adw_id=row["adw_id"],
            repo_url=row["repo_url"],
            issue_number=row["issue_number"],
            title=row["title"],
            workflow=row["workflow"],
            result=row["result"],
            tags=row["tags"],
            body=row["body"],
            markdown_path=row["markdown_path"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


# ---------------------------------------------------------------------------
# FTS query sanitization
# ---------------------------------------------------------------------------


_FTS_STRIP = re.compile(r"[^\w\s]", re.UNICODE)


def _sanitize_fts(query: str) -> str:
    """Turn arbitrary issue text into a safe FTS5 MATCH expression.

    FTS5 has a restrictive query language; we strip punctuation, collapse
    whitespace, drop ultra-short tokens, and OR the survivors so common
    words don't dominate.
    """
    cleaned = _FTS_STRIP.sub(" ", query or "")
    tokens = [t for t in cleaned.split() if len(t) >= 3]
    if not tokens:
        return ""
    # Use OR so any match scores; BM25 handles ranking
    return " OR ".join(f'"{t}"' for t in tokens[:20])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli() -> int:
    import argparse, sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from orchestrator.config import load_config
    from orchestrator.state_store import StateStore

    ap = argparse.ArgumentParser(prog="knowledge")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_search = sub.add_parser("search", help="Search lessons")
    p_search.add_argument("query")
    p_search.add_argument("--repo", help="Filter to a repo URL")
    p_search.add_argument("--limit", type=int, default=5)

    sub.add_parser("recent", help="List recent lessons")
    sub.add_parser("count", help="Count total lessons")

    ap.add_argument("--home", type=Path, default=None)
    args = ap.parse_args()

    cfg = load_config(args.home)
    store = StateStore(cfg.sqlite_path)
    kb = KnowledgeBase(store)

    if args.cmd == "search":
        results = kb.search(args.query, repo_url=args.repo, limit=args.limit)
        for r in results:
            print(f"[{r.adw_id}] {r.title}  ({r.result})  {r.repo_url}")
        print(f"({len(results)} results)")
    elif args.cmd == "recent":
        for r in kb.recent():
            print(f"[{r.adw_id}] {r.title}  ({r.result})  {r.repo_url}")
    elif args.cmd == "count":
        print(kb.count())
    return 0


if __name__ == "__main__":
    _cli()
