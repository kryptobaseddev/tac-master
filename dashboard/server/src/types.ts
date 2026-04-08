// Shared types for tac-master dashboard server.

export interface HookEvent {
  id?: number;
  repo_url?: string; // multi-repo aware — identifies which repo the agent is working on
  source_app: string; // e.g. "tac-master", "krypto-agent"
  session_id: string; // maps 1:1 to adw_id in most cases
  hook_event_type: string; // PreToolUse, PostToolUse, UserPromptSubmit, Stop, etc.
  payload: Record<string, unknown>;
  chat?: unknown[];
  summary?: string;
  timestamp?: number; // unix ms
  adw_id?: string; // denormalized from payload for fast filtering
  phase?: string; // optional phase label (plan, build, test, review, ...)
}

export type WsMessage =
  | { type: "initial"; data: HookEvent[] }
  | { type: "event"; data: HookEvent }
  | { type: "run_update"; data: RunSummary }
  | { type: "repo_status"; data: RepoStatus };

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

export interface FilterOptions {
  source_apps: string[];
  session_ids: string[];
  hook_event_types: string[];
  repo_urls: string[];
}
