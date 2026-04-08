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

export type WsMessage =
  | { type: "initial"; data: HookEvent[] }
  | { type: "event"; data: HookEvent }
  | { type: "run_update"; data: RunSummary }
  | { type: "repo_status"; data: RepoStatus };
