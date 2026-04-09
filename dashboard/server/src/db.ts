/**
 * Dashboard persistence.
 *
 * Writes hook events into its own SQLite DB (dashboard.sqlite) so the
 * Bun server remains stateless in-memory. Also opens the orchestrator's
 * tac_master.sqlite in read-only mode to expose per-repo status and run
 * information via the /api/repos and /api/runs endpoints.
 */

import { Database } from "bun:sqlite";
import type { HookEvent, RepoStatus, RunSummary, FilterOptions } from "./types";

const DASHBOARD_DB = process.env.TAC_DASHBOARD_DB ?? "./dashboard.sqlite";
const TAC_MASTER_DB =
  process.env.TAC_MASTER_DB ?? "../../state/tac_master.sqlite";

let eventsDb: Database | null = null;
let tacDb: Database | null = null;

// ---------------------------------------------------------------------------
// Events DB (owned by the dashboard)
// ---------------------------------------------------------------------------

export function initDatabase(): void {
  eventsDb = new Database(DASHBOARD_DB, { create: true });
  eventsDb.exec("PRAGMA journal_mode = WAL;");
  eventsDb.exec("PRAGMA synchronous = NORMAL;");
  eventsDb.exec(`
    CREATE TABLE IF NOT EXISTS events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      repo_url TEXT,
      source_app TEXT NOT NULL,
      session_id TEXT NOT NULL,
      hook_event_type TEXT NOT NULL,
      adw_id TEXT,
      phase TEXT,
      payload TEXT NOT NULL,
      chat TEXT,
      summary TEXT,
      timestamp INTEGER NOT NULL
    );
  `);
  eventsDb.exec("CREATE INDEX IF NOT EXISTS idx_events_repo ON events(repo_url);");
  eventsDb.exec("CREATE INDEX IF NOT EXISTS idx_events_source ON events(source_app);");
  eventsDb.exec("CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);");
  eventsDb.exec("CREATE INDEX IF NOT EXISTS idx_events_type ON events(hook_event_type);");
  eventsDb.exec("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp);");
  eventsDb.exec("CREATE INDEX IF NOT EXISTS idx_events_adw ON events(adw_id);");

  // Best-effort open of tac-master DB (read-only). If it's missing we
  // keep running; the /api/repos and /api/runs endpoints will return empty.
  try {
    tacDb = new Database(TAC_MASTER_DB, { readonly: true });
    tacDb.exec("PRAGMA query_only = ON;");
  } catch (e) {
    console.warn("[dashboard] tac_master.sqlite not found; run/repo endpoints disabled");
    tacDb = null;
  }
}

function db(): Database {
  if (!eventsDb) throw new Error("db not initialized");
  return eventsDb;
}

export function insertEvent(event: HookEvent): HookEvent {
  const now = event.timestamp ?? Date.now();
  const adwId = event.adw_id ?? (event.payload?.adw_id as string | undefined);
  const phase = event.phase ?? (event.payload?.phase as string | undefined);

  const stmt = db().prepare(`
    INSERT INTO events
      (repo_url, source_app, session_id, hook_event_type, adw_id, phase,
       payload, chat, summary, timestamp)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);
  const result = stmt.run(
    event.repo_url ?? null,
    event.source_app,
    event.session_id,
    event.hook_event_type,
    adwId ?? null,
    phase ?? null,
    JSON.stringify(event.payload ?? {}),
    event.chat ? JSON.stringify(event.chat) : null,
    event.summary ?? null,
    now,
  );

  return { ...event, id: Number(result.lastInsertRowid), timestamp: now,
           adw_id: adwId, phase };
}

export function getRecentEvents(limit = 100, repoUrl?: string): HookEvent[] {
  const query = repoUrl
    ? `SELECT * FROM events WHERE repo_url = ? ORDER BY timestamp DESC LIMIT ?`
    : `SELECT * FROM events ORDER BY timestamp DESC LIMIT ?`;
  const params = repoUrl ? [repoUrl, limit] : [limit];
  const rows = db().prepare(query).all(...params) as any[];
  return rows.reverse().map(rowToEvent);
}

export function getFilterOptions(): FilterOptions {
  const sourceApps = (
    db().prepare("SELECT DISTINCT source_app FROM events").all() as any[]
  ).map((r) => r.source_app).filter(Boolean);
  const sessionIds = (
    db()
      .prepare("SELECT DISTINCT session_id FROM events ORDER BY id DESC LIMIT 200")
      .all() as any[]
  ).map((r) => r.session_id).filter(Boolean);
  const eventTypes = (
    db().prepare("SELECT DISTINCT hook_event_type FROM events").all() as any[]
  ).map((r) => r.hook_event_type).filter(Boolean);
  const repoUrls = (
    db().prepare("SELECT DISTINCT repo_url FROM events WHERE repo_url IS NOT NULL").all() as any[]
  ).map((r) => r.repo_url);
  return {
    source_apps: sourceApps,
    session_ids: sessionIds,
    hook_event_types: eventTypes,
    repo_urls: repoUrls,
  };
}

function rowToEvent(row: any): HookEvent {
  return {
    id: row.id,
    repo_url: row.repo_url ?? undefined,
    source_app: row.source_app,
    session_id: row.session_id,
    hook_event_type: row.hook_event_type,
    adw_id: row.adw_id ?? undefined,
    phase: row.phase ?? undefined,
    payload: safeParse(row.payload),
    chat: row.chat ? safeParse(row.chat) : undefined,
    summary: row.summary ?? undefined,
    timestamp: row.timestamp,
  };
}

function safeParse(s: string | null): any {
  if (!s) return {};
  try {
    return JSON.parse(s);
  } catch {
    return {};
  }
}

// ---------------------------------------------------------------------------
// Read-only queries into the orchestrator's tac_master.sqlite
// ---------------------------------------------------------------------------

export function getRepoStatuses(): RepoStatus[] {
  if (!tacDb) return [];

  const todayStart = startOfTodayUnix();
  const repos = tacDb
    .prepare(
      `SELECT url, slug, is_self, default_workflow, model_set, auto_merge, last_polled_at
       FROM repos ORDER BY added_at ASC`,
    )
    .all() as any[];

  const out: RepoStatus[] = [];
  for (const r of repos) {
    const active = (tacDb
      .prepare(
        `SELECT COUNT(*) AS n FROM runs WHERE repo_url = ? AND status IN ('pending','running')`,
      )
      .get(r.url) as any).n as number;

    const completedToday = (tacDb
      .prepare(
        `SELECT COUNT(*) AS n FROM runs WHERE repo_url = ? AND status = 'succeeded' AND ended_at >= ?`,
      )
      .get(r.url, todayStart) as any).n as number;

    const failedToday = (tacDb
      .prepare(
        `SELECT COUNT(*) AS n FROM runs WHERE repo_url = ? AND status IN ('failed','aborted') AND ended_at >= ?`,
      )
      .get(r.url, todayStart) as any).n as number;

    const budget = tacDb
      .prepare(
        `SELECT tokens_used FROM budget_usage WHERE repo_url = ? AND day = date('now')`,
      )
      .get(r.url) as any;

    const ledger = tacDb
      .prepare(
        `SELECT IFNULL(SUM(cost_usd),0) AS cost, MAX(attributed_at) AS last_ts
         FROM token_ledger WHERE repo_url = ? AND attributed_at >= ?`,
      )
      .get(r.url, todayStart) as any;

    out.push({
      url: r.url,
      slug: r.slug,
      is_self: !!r.is_self,
      default_workflow: r.default_workflow,
      model_set: r.model_set,
      auto_merge: !!r.auto_merge,
      last_polled_at: r.last_polled_at,
      active_runs: active,
      completed_today: completedToday,
      failed_today: failedToday,
      tokens_today: budget?.tokens_used ?? 0,
      cost_today_usd: Number(ledger?.cost ?? 0),
      last_activity_at: ledger?.last_ts ?? r.last_polled_at ?? null,
    });
  }
  return out;
}

export function getActiveAndRecentRuns(limit = 50): RunSummary[] {
  if (!tacDb) return [];
  // T033: left-join token_ledger so input_tokens, output_tokens, and
  // total_cost_usd are populated once T032 backfills the ledger.
  // If token_ledger doesn't exist yet (older deployments), the query falls
  // back gracefully via the LEFT JOIN (values will be null → 0).
  let rows: any[];
  try {
    rows = tacDb
      .prepare(
        `SELECT r.*,
                COALESCE(tl.sum_input,  0) AS tl_input_tokens,
                COALESCE(tl.sum_output, 0) AS tl_output_tokens,
                COALESCE(tl.sum_cost,   0) AS tl_cost_usd
         FROM runs r
         LEFT JOIN (
           SELECT adw_id,
                  SUM(input_tokens)  AS sum_input,
                  SUM(output_tokens) AS sum_output,
                  SUM(cost_usd)      AS sum_cost
           FROM token_ledger
           GROUP BY adw_id
         ) tl ON tl.adw_id = r.adw_id
         ORDER BY
           CASE WHEN r.status IN ('pending','running') THEN 0 ELSE 1 END,
           COALESCE(r.started_at, 0) DESC
         LIMIT ?`,
      )
      .all(limit) as any[];
  } catch {
    // token_ledger table may not exist in older deployments — fall back
    rows = tacDb
      .prepare(
        `SELECT * FROM runs
         ORDER BY
           CASE WHEN status IN ('pending','running') THEN 0 ELSE 1 END,
           COALESCE(started_at, 0) DESC
         LIMIT ?`,
      )
      .all(limit) as any[];
  }
  return rows.map((r) => ({
    adw_id: r.adw_id,
    repo_url: r.repo_url,
    issue_number: r.issue_number,
    workflow: r.workflow,
    model_set: r.model_set,
    status: r.status,
    worktree_path: r.worktree_path,
    started_at: r.started_at,
    ended_at: r.ended_at,
    pid: r.pid,
    tokens_used: r.tokens_used ?? 0,
    input_tokens: r.tl_input_tokens ?? 0,
    output_tokens: r.tl_output_tokens ?? 0,
    total_cost_usd: r.tl_cost_usd ?? 0,
  }));
}

export function getLessons(limit = 20): Array<{
  adw_id: string;
  repo_url: string;
  title: string;
  result: string | null;
  created_at: number;
}> {
  if (!tacDb) return [];
  try {
    const rows = tacDb
      .prepare(
        `SELECT adw_id, repo_url, title, result, created_at
         FROM lessons ORDER BY created_at DESC LIMIT ?`,
      )
      .all(limit) as any[];
    return rows;
  } catch {
    return [];
  }
}

function startOfTodayUnix(): number {
  const now = new Date();
  return Math.floor(
    new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000,
  );
}
