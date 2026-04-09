/**
 * CLEO API — reads the CLEO tasks.db SQLite file and exposes
 * epics + tasks as JSON endpoints.
 *
 * Endpoints (wired into index.ts):
 *   GET /api/cleo/epics               — all epics with child-task progress
 *   GET /api/cleo/tasks?parent=TXXX   — tasks whose parent_id = TXXX
 *   GET /api/cleo/task/:id            — single task by ID
 *
 * DB path resolution (first that exists wins):
 *   1. CLEO_TASKS_DB env var
 *   2. /srv/tac-master/state/cleo-tasks.db  (LXC production copy)
 *   3. /mnt/projects/agentic-engineer/.cleo/tasks.db  (dev host direct)
 *
 * @task T040
 * @epic T036
 */

import { existsSync } from "node:fs";
import { Database } from "bun:sqlite";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface EpicProgress {
  total: number;
  done: number;
  active: number;
  pending: number;
  failed: number;
}

export interface EpicSummary {
  id: string;
  title: string;
  status: string;
  priority: string;
  size: string | null;
  labels: string[];
  progress: EpicProgress;
  pct: number; // 0-100 integer
}

export interface TaskSummary {
  id: string;
  title: string;
  status: string;
  type: string | null;
  priority: string;
  size: string | null;
  parent_id: string | null;
  labels: string[];
  acceptance: string[];
}

// ---------------------------------------------------------------------------
// DB path resolution
// ---------------------------------------------------------------------------

const DB_CANDIDATES = [
  process.env.CLEO_TASKS_DB,
  "/srv/tac-master/state/cleo-tasks.db",
  "/mnt/projects/agentic-engineer/.cleo/tasks.db",
].filter(Boolean) as string[];

function resolveDbPath(): string | null {
  for (const p of DB_CANDIDATES) {
    if (existsSync(p)) return p;
  }
  return null;
}

// ---------------------------------------------------------------------------
// Lazy singleton DB — reopen if the file path changes
// ---------------------------------------------------------------------------

let _db: Database | null = null;
let _dbPath: string | null = null;

function getDb(): Database | null {
  const path = resolveDbPath();
  if (!path) return null;
  if (_db && _dbPath === path) return _db;
  try {
    if (_db) {
      try { _db.close(); } catch { /* ignore */ }
    }
    _db = new Database(path, { readonly: true, create: false });
    _dbPath = path;
    console.log(`[cleo-api] opened ${path}`);
  } catch (e) {
    console.error("[cleo-api] failed to open DB:", e);
    _db = null;
    _dbPath = null;
  }
  return _db;
}

// ---------------------------------------------------------------------------
// Query helpers
// ---------------------------------------------------------------------------

function query<T>(sql: string, params: unknown[] = []): T[] | null {
  const db = getDb();
  if (!db) return null;
  try {
    return db.query(sql).all(...params) as T[];
  } catch (e) {
    console.error("[cleo-api] query error:", e);
    return null;
  }
}

function queryOne<T>(sql: string, params: unknown[] = []): T | null {
  const db = getDb();
  if (!db) return null;
  try {
    return (db.query(sql).get(...params) ?? null) as T | null;
  } catch (e) {
    console.error("[cleo-api] queryOne error:", e);
    return null;
  }
}

// ---------------------------------------------------------------------------
// Data helpers
// ---------------------------------------------------------------------------

function parseJson<T>(raw: string | null | undefined, fallback: T): T {
  if (!raw) return fallback;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

function rowToTask(r: Record<string, unknown>): TaskSummary {
  return {
    id: String(r.id),
    title: String(r.title ?? ""),
    status: String(r.status ?? "pending"),
    type: r.type ? String(r.type) : null,
    priority: String(r.priority ?? "medium"),
    size: r.size ? String(r.size) : null,
    parent_id: r.parent_id ? String(r.parent_id) : null,
    labels: parseJson<string[]>(r.labels_json as string | null, []),
    acceptance: parseJson<string[]>(r.acceptance_json as string | null, []),
  };
}

const DONE_STATUSES = new Set(["done", "completed", "succeeded"]);
const ACTIVE_STATUSES = new Set(["active", "in_progress", "running", "in-progress"]);
const FAILED_STATUSES = new Set(["failed", "blocked", "cancelled", "canceled"]);

function buildProgress(children: TaskSummary[]): EpicProgress {
  const p: EpicProgress = { total: children.length, done: 0, active: 0, pending: 0, failed: 0 };
  for (const t of children) {
    const s = t.status.toLowerCase();
    if (DONE_STATUSES.has(s)) p.done++;
    else if (ACTIVE_STATUSES.has(s)) p.active++;
    else if (FAILED_STATUSES.has(s)) p.failed++;
    else p.pending++;
  }
  return p;
}

// ---------------------------------------------------------------------------
// Public API functions (called from index.ts route handler)
// ---------------------------------------------------------------------------

/**
 * Returns all epics sorted by priority, each with child-task progress.
 * Used by GET /api/cleo/epics
 */
export function getEpics(): { epics: EpicSummary[]; dbPath: string | null; error?: string } {
  const dbPath = resolveDbPath();

  const rows = query<Record<string, unknown>>(
    `SELECT id, title, status, priority, size, labels_json
     FROM tasks
     WHERE type = 'epic'
       AND (archived_at IS NULL OR archived_at = '')
     ORDER BY
       CASE priority
         WHEN 'critical' THEN 0
         WHEN 'high'     THEN 1
         WHEN 'medium'   THEN 2
         WHEN 'low'      THEN 3
         ELSE 4
       END,
       created_at ASC`,
  );

  if (rows === null) {
    return {
      epics: [],
      dbPath,
      error: dbPath
        ? "Failed to query tasks.db"
        : "tasks.db not found — set CLEO_TASKS_DB or copy to /srv/tac-master/state/cleo-tasks.db",
    };
  }

  const epics: EpicSummary[] = rows.map((r) => {
    const children = getTasksByParent(String(r.id));
    const progress = buildProgress(children);
    const pct = progress.total > 0 ? Math.round((progress.done / progress.total) * 100) : 0;
    return {
      id: String(r.id),
      title: String(r.title ?? ""),
      status: String(r.status ?? "pending"),
      priority: String(r.priority ?? "medium"),
      size: r.size ? String(r.size) : null,
      labels: parseJson<string[]>(r.labels_json as string | null, []),
      progress,
      pct,
    };
  });

  return { epics, dbPath };
}

/**
 * Returns direct children of a parent task.
 * Used by GET /api/cleo/tasks?parent=TXXX
 */
export function getTasksByParent(parentId: string): TaskSummary[] {
  const rows = query<Record<string, unknown>>(
    `SELECT id, title, status, type, priority, size, parent_id, labels_json, acceptance_json
     FROM tasks
     WHERE parent_id = ?
       AND (archived_at IS NULL OR archived_at = '')
     ORDER BY position ASC, created_at ASC`,
    [parentId],
  );
  if (!rows) return [];
  return rows.map(rowToTask);
}

/**
 * Returns a single task by ID.
 * Used by GET /api/cleo/task/:id
 */
export function getTaskById(id: string): TaskSummary | null {
  const row = queryOne<Record<string, unknown>>(
    `SELECT id, title, status, type, priority, size, parent_id, labels_json, acceptance_json
     FROM tasks WHERE id = ?`,
    [id],
  );
  return row ? rowToTask(row) : null;
}
