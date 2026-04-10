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

export function getEventsByAdwId(adwId: string, limit = 200): HookEvent[] {
  const rows = db()
    .prepare(
      `SELECT * FROM events WHERE adw_id = ? ORDER BY timestamp ASC LIMIT ?`,
    )
    .all(adwId, limit) as any[];
  return rows.map(rowToEvent);
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

// ---------------------------------------------------------------------------
// Phase breakdown for a specific run (T038)
// ---------------------------------------------------------------------------

export interface PhaseEntry {
  phase: string;
  status: string;
  started?: number | null;
  ended?: number | null;
}

/**
 * Returns the phases touched by a run derived from two sources:
 * 1. The dashboard events DB (hook_events with phase+adw_id set)
 * 2. The tac_master runs table to determine terminal status
 *
 * Phase status inference:
 *  - The last known event for each phase determines "started".
 *  - If the run has ended (succeeded/failed/aborted) AND there are no later
 *    phases, the last seen phase is marked as the run's terminal status.
 *  - A phase is "active" only when it matches the most recent event overall.
 *  - All earlier phases are "completed".
 */
const PITER_PHASES = [
  "classify_iso",
  "plan_iso",
  "build_iso",
  "test_iso",
  "review_iso",
  "document_iso",
  "ship_iso",
  "reflect_iso",
];

export function getRunPhases(adwId: string): PhaseEntry[] {
  // Query dashboard events DB for phases seen for this run
  type PhaseRow = { phase: string; first_ts: number; last_ts: number };
  const rows = db()
    .prepare(
      `SELECT phase,
              MIN(timestamp) AS first_ts,
              MAX(timestamp) AS last_ts
       FROM events
       WHERE adw_id = ?
         AND phase IS NOT NULL
       GROUP BY phase
       ORDER BY first_ts ASC`,
    )
    .all(adwId) as PhaseRow[];

  if (rows.length === 0) {
    // No events recorded yet — return all phases as pending
    return PITER_PHASES.map((p) => ({ phase: p, status: "pending" }));
  }

  // Determine run's terminal status from tac_master DB if available
  let runStatus = "running";
  if (tacDb) {
    try {
      const run = tacDb
        .prepare(`SELECT status FROM runs WHERE adw_id = ? LIMIT 1`)
        .get(adwId) as { status: string } | undefined;
      if (run) runStatus = run.status;
    } catch {
      // ignore
    }
  }

  const seenPhases = rows.map((r) => r.phase);
  const lastSeenPhase = seenPhases[seenPhases.length - 1];
  const mostRecentTs = rows[rows.length - 1]?.last_ts ?? 0;

  // Build a lookup of normalised phase names from events
  const phaseMap = new Map<string, PhaseRow>();
  for (const r of rows) {
    phaseMap.set(normPhase(r.phase), r);
  }

  return PITER_PHASES.map((piterKey) => {
    const row = phaseMap.get(piterKey);
    if (!row) {
      // Not yet reached
      return { phase: piterKey, status: "pending" };
    }

    const isLast = normPhase(lastSeenPhase) === piterKey;
    let status: string;

    if (isLast) {
      if (runStatus === "succeeded") {
        status = "completed";
      } else if (runStatus === "failed" || runStatus === "aborted") {
        status = "failed";
      } else {
        // still running — this is the active phase if its last event is the
        // most recent overall; otherwise treat as completed
        status = row.last_ts === mostRecentTs ? "running" : "completed";
      }
    } else {
      status = "completed";
    }

    return {
      phase: piterKey,
      status,
      started: row.first_ts,
      ended: isLast && runStatus !== "running" ? row.last_ts : null,
    };
  });
}

function normPhase(raw: string): string {
  if (!raw) return raw;
  if (raw.endsWith("_iso")) return raw;
  return `${raw}_iso`;
}

// ---------------------------------------------------------------------------
// Phase summary for /api/runs/:adw_id/phase/:phase/summary (T055)
// ---------------------------------------------------------------------------

export interface PhaseSummary {
  phase: string;
  adw_id: string;
  status: string;
  duration_seconds: number | null;
  first_event_ts: number | null;
  last_event_ts: number | null;
  event_counts: Record<string, number>;
  total_events: number;
  artifacts: PhaseArtifacts;
}

export interface PhaseArtifacts {
  // classify
  classified_as?: string;
  // plan
  spec_file?: string;
  // build
  branch?: string;
  commits?: number;
  files_changed?: number;
  commit_message?: string;
  // test
  passed?: number;
  failed?: number;
  auto_fixed?: number;
  // review
  review_status?: string;
  // document
  docs_committed?: boolean;
  // ship
  pr_number?: number;
  pr_url?: string;
  merged?: boolean;
  // reflect
  lesson_written?: boolean;
  lesson_summary?: string;
}

/**
 * Derive phase artifacts from the events payload JSON column.
 * The payload field varies per hook event type, so we look for
 * relevant keys across all events for this adw_id + phase.
 */
function getPhaseArtifacts(adwId: string, phase: string): PhaseArtifacts {
  const normKey = normPhase(phase);
  const phaseLabel = normKey.replace("_iso", "");

  // Fetch all payload strings for this adw_id + phase combo
  type PayloadRow = { payload: string };
  const payloadRows = db()
    .prepare(
      `SELECT payload FROM events
       WHERE adw_id = ? AND (phase = ? OR phase = ?)
       ORDER BY timestamp ASC`,
    )
    .all(adwId, phase, normKey) as PayloadRow[];

  const payloads: Array<Record<string, unknown>> = payloadRows
    .map((r) => {
      try { return JSON.parse(r.payload) as Record<string, unknown>; }
      catch { return {}; }
    });

  const art: PhaseArtifacts = {};

  // Helper: find first non-empty string value across all payloads
  function findStr(key: string): string | undefined {
    for (const p of payloads) {
      const v = p[key];
      if (typeof v === "string" && v.trim()) return v.trim();
    }
    return undefined;
  }
  function findNum(key: string): number | undefined {
    for (const p of payloads) {
      const v = p[key];
      if (typeof v === "number") return v;
      if (typeof v === "string" && /^\d+$/.test(v)) return Number(v);
    }
    return undefined;
  }
  function findBool(key: string): boolean | undefined {
    for (const p of payloads) {
      if (key in p) return !!p[key];
    }
    return undefined;
  }

  switch (phaseLabel) {
    case "classify": {
      const cls = findStr("classified_as") ?? findStr("classification") ?? findStr("issue_type");
      if (cls) art.classified_as = cls;
      break;
    }
    case "plan": {
      const sf = findStr("spec_file") ?? findStr("spec_path") ?? findStr("plan_file");
      if (sf) art.spec_file = sf;
      break;
    }
    case "build": {
      const branch = findStr("branch") ?? findStr("branch_name");
      if (branch) art.branch = branch;
      const commits = findNum("commits") ?? findNum("commit_count");
      if (commits != null) art.commits = commits;
      const files = findNum("files_changed") ?? findNum("files");
      if (files != null) art.files_changed = files;
      const msg = findStr("commit_message") ?? findStr("commit_msg");
      if (msg) art.commit_message = msg;
      break;
    }
    case "test": {
      const passed = findNum("passed") ?? findNum("tests_passed");
      if (passed != null) art.passed = passed;
      const failed = findNum("failed") ?? findNum("tests_failed");
      if (failed != null) art.failed = failed;
      const fixed = findNum("auto_fixed") ?? findNum("fixed");
      if (fixed != null) art.auto_fixed = fixed;
      break;
    }
    case "review": {
      const rs = findStr("review_status") ?? findStr("review_result");
      if (rs) art.review_status = rs;
      break;
    }
    case "document": {
      const dc = findBool("docs_committed") ?? findBool("docs_written");
      if (dc != null) art.docs_committed = dc;
      break;
    }
    case "ship": {
      const prNum = findNum("pr_number") ?? findNum("pr_num");
      if (prNum != null) art.pr_number = prNum;
      const prUrl = findStr("pr_url") ?? findStr("pull_request_url");
      if (prUrl) art.pr_url = prUrl;
      const merged = findBool("merged") ?? findBool("pr_merged");
      if (merged != null) art.merged = merged;
      break;
    }
    case "reflect": {
      const lw = findBool("lesson_written") ?? findBool("lesson_saved");
      if (lw != null) art.lesson_written = lw;
      const ls = findStr("lesson_summary") ?? findStr("lesson");
      if (ls) art.lesson_summary = ls;
      break;
    }
  }

  // If we got nothing from events, try the tac_master lessons table for reflect phase
  if (phaseLabel === "reflect" && !art.lesson_summary && tacDb) {
    try {
      const lesson = tacDb
        .prepare(`SELECT title, result FROM lessons WHERE adw_id = ? ORDER BY created_at DESC LIMIT 1`)
        .get(adwId) as { title: string; result: string | null } | undefined;
      if (lesson) {
        art.lesson_written = true;
        art.lesson_summary = lesson.result ?? lesson.title;
      }
    } catch {
      // ignore
    }
  }

  // For build phase: try to pick up branch info from run's worktree_path
  if (phaseLabel === "build" && !art.branch && tacDb) {
    try {
      const run = tacDb
        .prepare(`SELECT worktree_path FROM runs WHERE adw_id = ? LIMIT 1`)
        .get(adwId) as { worktree_path: string | null } | undefined;
      if (run?.worktree_path) {
        // worktree_path often ends in a branch name component
        const parts = run.worktree_path.split("/");
        if (parts.length > 0) art.branch = parts[parts.length - 1];
      }
    } catch {
      // ignore
    }
  }

  return art;
}

export function getRunPhaseSummary(adwId: string, phase: string): PhaseSummary {
  const normKey = normPhase(phase);

  // Event counts by type, with timestamps
  type EventRow = { hook_event_type: string; count: number; first_ts: number; last_ts: number };
  const eventRows = db()
    .prepare(
      `SELECT hook_event_type,
              COUNT(*) AS count,
              MIN(timestamp) AS first_ts,
              MAX(timestamp) AS last_ts
       FROM events
       WHERE adw_id = ? AND (phase = ? OR phase = ?)
       GROUP BY hook_event_type`,
    )
    .all(adwId, phase, normKey) as EventRow[];

  const eventCounts: Record<string, number> = {};
  let totalEvents = 0;
  let firstTs: number | null = null;
  let lastTs: number | null = null;

  for (const row of eventRows) {
    eventCounts[row.hook_event_type] = row.count;
    totalEvents += row.count;
    if (firstTs == null || row.first_ts < firstTs) firstTs = row.first_ts;
    if (lastTs == null || row.last_ts > lastTs) lastTs = row.last_ts;
  }

  // Duration in seconds (timestamps are ms-epoch if > 1e12, else seconds)
  let durationSeconds: number | null = null;
  if (firstTs != null && lastTs != null && lastTs > firstTs) {
    const diff = lastTs - firstTs;
    // Detect ms-epoch (> year 2001 in ms)
    durationSeconds = diff > 1e9 ? Math.round(diff / 1000) : diff;
  }

  // Derive status from run status + phase position
  const phases = getRunPhases(adwId);
  const found = phases.find((p) => p.phase === normKey || p.phase === phase);
  const status = found?.status ?? (totalEvents > 0 ? "completed" : "pending");

  const artifacts = getPhaseArtifacts(adwId, phase);

  return {
    phase: normKey,
    adw_id: adwId,
    status,
    duration_seconds: durationSeconds,
    first_event_ts: firstTs,
    last_event_ts: lastTs,
    event_counts: eventCounts,
    total_events: totalEvents,
    artifacts,
  };
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

// ---------------------------------------------------------------------------
// Aggregate KPI stats for /api/stats (T037 Command Center status bar)
// ---------------------------------------------------------------------------

export interface AggregateStats {
  live_runs: number;
  total_repos: number;
  tokens_today: number;
  cost_today_usd: number;
  total_runs: number;
}

export function getAggregateStats(): AggregateStats {
  const empty: AggregateStats = {
    live_runs: 0,
    total_repos: 0,
    tokens_today: 0,
    cost_today_usd: 0,
    total_runs: 0,
  };

  if (!tacDb) return empty;

  try {
    const todayStart = startOfTodayUnix();

    const liveRow = tacDb
      .prepare(`SELECT COUNT(*) AS n FROM runs WHERE status IN ('pending','running')`)
      .get() as any;

    const repoRow = tacDb
      .prepare(`SELECT COUNT(*) AS n FROM repos`)
      .get() as any;

    const runsRow = tacDb
      .prepare(`SELECT COUNT(*) AS n FROM runs`)
      .get() as any;

    // token_ledger may not exist in older deployments — wrap in try/catch
    let tokensRow: any = { tokens: 0 };
    let costRow: any = { cost: 0 };
    try {
      tokensRow = tacDb
        .prepare(
          `SELECT IFNULL(SUM(input_tokens + output_tokens), 0) AS tokens
           FROM token_ledger WHERE attributed_at >= ?`,
        )
        .get(todayStart) as any;
      costRow = tacDb
        .prepare(
          `SELECT IFNULL(SUM(cost_usd), 0) AS cost
           FROM token_ledger WHERE attributed_at >= ?`,
        )
        .get(todayStart) as any;
    } catch {
      // token_ledger doesn't exist yet — fall back to budget_usage
      try {
        tokensRow = tacDb
          .prepare(
            `SELECT IFNULL(SUM(tokens_used), 0) AS tokens
             FROM budget_usage WHERE day = date('now')`,
          )
          .get() as any;
      } catch {
        // budget_usage also missing — leave zeros
      }
    }

    return {
      live_runs:      Number(liveRow?.n ?? 0),
      total_repos:    Number(repoRow?.n ?? 0),
      tokens_today:   Number(tokensRow?.tokens ?? 0),
      cost_today_usd: Number(costRow?.cost ?? 0),
      total_runs:     Number(runsRow?.n ?? 0),
    };
  } catch (e) {
    console.error("[stats] aggregate query failed:", e);
    return empty;
  }
}
