/**
 * WebSocket event types for tac-master streaming pipeline (T058 spec).
 * These mirror the server WsMessage union types for frontend consumption.
 */

/**
 * Thinking block event — emitted when Claude generates thinking.
 */
export interface ThinkingBlockEvent {
  type: "thinking_block";
  data: {
    adw_id: string;
    session_id: string;
    phase: string;
    thinking: string; // the thinking text content
    timestamp: number;
  };
}

/**
 * Tool use block event — emitted when Claude calls a tool.
 */
export interface ToolUseBlockEvent {
  type: "tool_use_block";
  data: {
    adw_id: string;
    session_id: string;
    phase: string;
    tool_name: string;
    tool_input: Record<string, unknown>;
    timestamp: number;
  };
}

/**
 * Text block event — emitted when Claude generates text output.
 */
export interface TextBlockEvent {
  type: "text_block";
  data: {
    adw_id: string;
    session_id: string;
    phase: string;
    text: string;
    timestamp: number;
  };
}

/**
 * Agent status event — emitted for hook lifecycle events (PreToolUse, PostToolUse, Stop, etc.)
 */
export interface AgentStatusEvent {
  type: "agent_status";
  data: {
    adw_id: string;
    session_id: string;
    hook_event_type: string; // PreToolUse, PostToolUse, Stop, etc.
    phase: string;
    timestamp: number;
  };
}

/**
 * Heartbeat event — replaces the polling loop, broadcasts periodically.
 */
export interface HeartbeatEvent {
  type: "heartbeat";
  data: {
    timestamp: number;
    active_clients: number;
  };
}

/**
 * Legacy event types (existing for backward compatibility).
 */
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

export interface InitialEvent {
  type: "initial";
  data: HookEvent[];
}

export interface EventMessage {
  type: "event";
  data: HookEvent;
}

export interface RunUpdateEvent {
  type: "run_update";
  data: RunSummary;
}

export interface RepoStatusEvent {
  type: "repo_status";
  data: RepoStatus;
}

/**
 * Union type for all possible WebSocket messages from the server.
 */
export type WsMessage =
  | InitialEvent
  | EventMessage
  | RunUpdateEvent
  | RepoStatusEvent
  | ThinkingBlockEvent
  | ToolUseBlockEvent
  | TextBlockEvent
  | AgentStatusEvent
  | HeartbeatEvent;
