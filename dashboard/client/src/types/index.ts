/**
 * Unified type exports for the tac-master dashboard.
 *
 * - `orchestrator-types.d.ts` was copied verbatim from orchestrator_3_stream
 *   and defines the Agent/AgentLog/EventStreamEntry shapes that the ported
 *   Vue components expect.
 * - The local `tac-master` types below are what tac-master's backend
 *   actually returns. The store adapts between them.
 *
 * All downstream code imports from here via `import { ... } from "../types"`.
 */

// Re-export everything from the orchestrator-ported types
export * from "./orchestrator-types";

// ---- tac-master-native shapes (come straight from the Bun server) ----

export interface HookEvent {
  id?: number;
  repo_url?: string;
  source_app: string;
  session_id: string;
  hook_event_type: string;
  adw_id?: string;
  phase?: string;
  payload: Record<string, unknown>;
  chat?: unknown[];
  summary?: string;
  timestamp?: number;
}

export interface RunSummary {
  adw_id: string;
  repo_url: string;
  issue_number: number;
  workflow: string;
  model_set: string;
  status: string;
  worktree_path?: string | null;
  started_at?: number | null;
  ended_at?: number | null;
  pid?: number | null;
  tokens_used: number;
}

export interface RepoStatus {
  url: string;
  slug: string;
  is_self: boolean;
  default_workflow: string;
  model_set: string;
  auto_merge: boolean;
  last_polled_at: number | null;
  active_runs: number;
  completed_today: number;
  failed_today: number;
  tokens_today: number;
  cost_today_usd: number;
  last_activity_at: number | null;
}

export type TacWsMessage =
  | { type: "initial"; data: HookEvent[] }
  | { type: "event"; data: HookEvent }
  | { type: "run_update"; data: RunSummary }
  | { type: "repo_status"; data: RepoStatus };
