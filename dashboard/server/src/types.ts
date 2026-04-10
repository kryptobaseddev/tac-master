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

// Streaming block types for WebSocket events (T058 spec)
export interface ThinkingBlockData {
  adw_id: string;
  session_id: string;
  phase: string;
  thinking: string; // the thinking text content
  timestamp: number;
}

export interface ToolUseBlockData {
  adw_id: string;
  session_id: string;
  phase: string;
  tool_name: string;
  tool_input: Record<string, unknown>;
  timestamp: number;
}

export interface TextBlockData {
  adw_id: string;
  session_id: string;
  phase: string;
  text: string;
  timestamp: number;
}

export interface AgentStatusData {
  adw_id: string;
  session_id: string;
  hook_event_type: string; // PreToolUse, PostToolUse, Stop, etc.
  phase: string;
  timestamp: number;
}

export interface HeartbeatData {
  timestamp: number;
  active_clients: number;
}

export interface ChatMessage {
  id: string;
  orchestrator_agent_id: string;
  sender_type: "user" | "orchestrator";
  receiver_type: string;
  message: string;
  metadata: Record<string, unknown>;
  summary?: string | null;
  created_at: number;
  updated_at: number;
}

export type WsMessage =
  | { type: "initial"; data: HookEvent[] }
  | { type: "event"; data: HookEvent }
  | { type: "run_update"; data: RunSummary }
  | { type: "repo_status"; data: RepoStatus }
  | { type: "thinking_block"; data: ThinkingBlockData }
  | { type: "tool_use_block"; data: ToolUseBlockData }
  | { type: "text_block"; data: TextBlockData }
  | { type: "agent_status"; data: AgentStatusData }
  | { type: "heartbeat"; data: HeartbeatData }
  | { type: "orchestrator_chat"; message: ChatMessage }
  | { type: "chat_typing"; orchestrator_agent_id: string; is_typing: boolean }
  | { type: "chat_stream"; chunk: string; is_complete: boolean };

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
  // T033: token ledger attribution fields (populated when T032 backfills ledger)
  input_tokens?: number;
  output_tokens?: number;
  total_cost_usd?: number;
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
