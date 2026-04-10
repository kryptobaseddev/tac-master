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

// ---- Swimlane & Observability Types (T122) ----

/**
 * AdwSummary: Swimlane-compatible shape representing a workflow run.
 * Adapted from RunSummary with tac-master column names.
 */
export interface AdwSummary {
  id: string; // maps to adw_id in runs table
  workflow_type: string; // maps to workflow column
  adw_name: string;
  status: string; // from runs.status enum
  started_at?: number | null;
  duration_seconds?: number | null;
  events_by_step?: Record<string, Array<{
    id: string;
    adw_id: string;
    adw_step: string | null;
    event_category: string;
    event_type: string;
    summary: string | null;
    payload: Record<string, unknown> | null;
    timestamp: string;
  }>>;
}

/**
 * CostMetrics: Session cost tracking and burn rate analysis.
 */
export interface CostMetrics {
  sessionCost: number; // USD
  inputTokens: number;
  outputTokens: number;
  totalCost: number; // USD
  burnRatePerHour: number; // USD/hour
}

/**
 * SystemInfo: Orchestrator system information for CommandPalette and UI.
 */
export interface SystemInfo {
  session_id: string;
  working_dir: string;
  slash_commands: Array<{
    name: string;
    description: string;
  }>;
  adw_workflows: Array<{
    name: string;
    display_name: string;
    description: string;
  }>;
  orchestrator_tools: string[];
}

/**
 * AdwWorkflowDef: Workflow definition with display metadata.
 */
export interface AdwWorkflowDef {
  name: string;
  display_name: string;
  description: string;
}

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
  // T033: token ledger attribution (populated once T032 backfills ledger)
  input_tokens?: number;
  output_tokens?: number;
  total_cost_usd?: number;
  // CLEO task association (optional)
  cleo_task_id?: string | null;
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

// Chat WS event types (T089 — orchestrator_chat messages)
export interface ChatWsMessage {
  type: "orchestrator_chat";
  message: {
    id: string;
    orchestrator_agent_id: string;
    sender_type: "user" | "orchestrator";
    receiver_type: "user" | "orchestrator";
    message: string;
    metadata: Record<string, any>;
    timestamp: number;
  };
}

// Thinking block WS event (T089)
export interface ThinkingBlockWsMessage {
  type: "thinking_block";
  data: {
    id: string;
    orchestrator_agent_id: string;
    thinking: string;
    timestamp: number;
  };
}

// Tool use block WS event (T089)
export interface ToolUseBlockWsMessage {
  type: "tool_use_block";
  data: {
    id: string;
    orchestrator_agent_id: string;
    tool_name: string;
    tool_input: Record<string, any>;
    tool_use_id: string;
    timestamp: number;
  };
}

// Chat typing indicator WS event (T089)
export interface ChatTypingWsMessage {
  type: "chat_typing";
  orchestrator_agent_id: string;
  is_typing: boolean;
}

// Chat stream complete WS event (T089)
export interface ChatStreamWsMessage {
  type: "chat_stream";
  chunk: string;
  is_complete: boolean;
}

export type TacWsMessage =
  | { type: "initial"; data: HookEvent[] }
  | { type: "event"; data: HookEvent }
  | { type: "run_update"; data: RunSummary }
  | { type: "repo_status"; data: RepoStatus }
  | ChatWsMessage
  | ThinkingBlockWsMessage
  | ToolUseBlockWsMessage
  | ChatTypingWsMessage
  | ChatStreamWsMessage;
