/**
 * Pinia store for the tac-master dashboard.
 *
 * The shape of this store matches what the Vue components ported from
 * orchestrator_3_stream expect (Agent[], EventStreamEntry[], selectedAgent,
 * filteredEventStream, stats, autoScroll, …). Data is loaded from
 * tac-master's native endpoints (/api/runs, /events/recent, /api/repos)
 * and mapped into those shapes on the fly.
 *
 * ADAPTATION LAYER:
 *   tac-master Run   → Agent
 *   tac-master Event → AgentLog → EventStreamEntry (row rendering)
 *   tac-master Repo  → kept as a separate `repos` state slice
 *
 * @task T033
 * @epic T028
 * @why T030 audit found hookEventTypeToCategory() returned "hook" for every
 *      event, making the HOOK counter show all events and THINKING always 0.
 *      Event categories are now semantically correct per hook type.
 * @what Fixed hookEventTypeToCategory and hookToAgentLog to map PreToolUse/
 *       PostToolUse → "tool", Stop/SubagentStop/Notification/PreCompact/
 *       UserPromptSubmit → "hook", and documented that "thinking" and
 *       "response" events cannot be sourced from Claude Code hooks alone —
 *       they require stream-json parsing (TODO: wire at ingest time).
 */

import { defineStore } from "pinia";
import { ref, computed } from "vue";
import { useAgentPulse } from "../composables/useAgentPulse";
import type {
  Agent,
  AgentLog,
  AgentStatus,
  AppStats,
  EventCategory,
  EventStreamEntry,
  LogLevel,
  OrchestratorAgent,
  // tac-master native
  HookEvent,
  RunSummary,
  RepoStatus,
  TacWsMessage,
  ThinkingBlockWsMessage,
  ToolUseBlockWsMessage,
} from "../types";

const MAX_EVENT_STREAM_SIZE = 2000;
const MAX_STREAMING_BLOCK_SIZE = 500;

// ---------------------------------------------------------------------------
// Mapping helpers: tac-master → orchestrator shapes
// ---------------------------------------------------------------------------

function runStatusToAgent(status: string): AgentStatus {
  switch (status) {
    case "pending":
      return "waiting";
    case "running":
      return "executing";
    case "succeeded":
    case "failed":
    case "aborted":
      return "complete";
    default:
      return "idle";
  }
}

function runModelFor(modelSet: string): string {
  return modelSet === "heavy"
    ? "claude-opus-4-6"
    : modelSet === "haiku"
      ? "claude-haiku-4-5"
      : "claude-sonnet-4-6";
}

function runToAgent(run: RunSummary): Agent {
  const started = run.started_at ? new Date(run.started_at * 1000).toISOString() : new Date().toISOString();
  const ended = run.ended_at ? new Date(run.ended_at * 1000).toISOString() : started;
  const slug = run.repo_url.replace("https://github.com/", "").replace(/\.git$/, "");
  return {
    id: run.adw_id,
    name: `${run.workflow} #${run.issue_number}`,
    model: runModelFor(run.model_set),
    system_prompt: null,
    working_dir: run.worktree_path ?? null,
    git_worktree: run.worktree_path ?? null,
    status: runStatusToAgent(run.status),
    session_id: run.adw_id,
    adw_id: run.adw_id,
    adw_step: run.workflow,
    // T033: use token_ledger totals when available (T032 populates these).
    // Falls back to tokens_used (output-only from the runs table) and 0 cost
    // for deployments where T032 hasn't landed yet.
    input_tokens: run.input_tokens ?? 0,
    output_tokens: run.output_tokens ?? run.tokens_used,
    total_cost: run.total_cost_usd ?? 0,
    archived: run.status === "succeeded" || run.status === "failed" || run.status === "aborted",
    metadata: {
      template_name: run.workflow,
      template_color: run.model_set === "heavy" ? "#a855f7" : "#3b82f6",
      repo_url: run.repo_url,
      repo_slug: slug,
      issue_number: run.issue_number,
      pid: run.pid ?? undefined,
      started_at_unix: run.started_at,
      ended_at_unix: run.ended_at,
      runtime: "native",
      run_status: run.status,
      cleo_task_id: run.cleo_task_id ?? undefined,
    },
    task: `${slug} · issue #${run.issue_number}`,
    log_count: 0,
    latest_summary: `${run.workflow} → ${run.status}`,
    created_at: started,
    updated_at: ended,
  };
}

/**
 * Map a tac-master hook_event_type to an EventCategory.
 *
 * Semantic buckets (T033 fix — previously everything returned "hook"):
 *   "tool"     → PreToolUse, PostToolUse
 *   "hook"     → lifecycle events: UserPromptSubmit, Stop, SubagentStop,
 *                Notification, PreCompact, and any unrecognised hook type
 *
 * NOTE: "response" (Claude's assistant text) and "thinking" (reasoning blocks)
 * are NOT produced by Claude Code hooks. They live inside the stream-json file
 * that Claude Code writes to <worktree>/agents/<adw_id>/<phase>/cc_raw_output.jsonl.
 * TODO: parse cc_raw_output.jsonl at ingest time in the dashboard server and
 * emit synthetic hook events with hook_event_type="AssistantResponse" and
 * hook_event_type="ThinkingBlock" so those categories can be surfaced here.
 */
function hookEventTypeToCategory(t: string): EventCategory {
  switch (t) {
    case "PreToolUse":
    case "PostToolUse":
      return "tool";
    default:
      // UserPromptSubmit, Stop, SubagentStop, Notification, PreCompact, etc.
      return "hook";
  }
}

function hookEventTypeToLevel(t: string): LogLevel | "SUCCESS" {
  const v = (t || "").toLowerCase();
  if (v.includes("error") || v.includes("fail")) return "ERROR";
  if (v.includes("warn")) return "WARNING";
  if (v === "stop" || v === "subagentstop") return "SUCCESS";
  return "INFO";
}

function summariseHookPayload(h: HookEvent): string {
  const p: any = h.payload || {};
  if (h.summary) return h.summary;
  if (h.hook_event_type === "PreToolUse" || h.hook_event_type === "PostToolUse") {
    const tool = p.tool_name || "(tool)";
    const target =
      p.tool_input?.file_path ||
      p.tool_input?.command ||
      p.tool_input?.pattern ||
      p.tool_input?.url ||
      "";
    return target ? `${tool}  ${String(target).slice(0, 120)}` : String(tool);
  }
  if (h.hook_event_type === "UserPromptSubmit") {
    return String(p.prompt || "").slice(0, 180);
  }
  if (h.hook_event_type === "Stop") {
    return "Session stopped";
  }
  if (h.hook_event_type === "SubagentStop") {
    return "Subagent stopped";
  }
  if (h.hook_event_type === "Notification") {
    return String(p.message || "notification");
  }
  return h.hook_event_type;
}

function hookToAgentLog(h: HookEvent): AgentLog {
  const p: any = h.payload || {};
  const ts = h.timestamp ? new Date(h.timestamp).toISOString() : new Date().toISOString();
  return {
    id: String(h.id ?? `${h.session_id}-${ts}`),
    agent_id: h.adw_id || h.session_id || "unknown",
    agent_name: h.phase || h.adw_id || h.session_id,
    session_id: h.session_id || null,
    task_slug: h.phase ?? null,
    entry_index: null,
    event_category: hookEventTypeToCategory(h.hook_event_type),
    // event_type drives the per-row category badge in AgentLogRow.vue.
    // - PreToolUse / PostToolUse → "tool_use"  (TOOL category)
    // - All lifecycle hooks (UserPromptSubmit, Stop, SubagentStop,
    //   Notification, PreCompact, …) → lowercased hook_event_type (HOOK cat)
    // NOTE: "text" / "thinking" event_types would be emitted by synthetic
    // AssistantResponse / ThinkingBlock events from cc_raw_output.jsonl
    // parsing — not yet implemented; see hookEventTypeToCategory TODO above.
    event_type: h.hook_event_type === "PreToolUse" || h.hook_event_type === "PostToolUse"
      ? "tool_use"
      : h.hook_event_type.toLowerCase(),
    content: summariseHookPayload(h),
    payload: {
      ...p,
      hook_event_type: h.hook_event_type,
      repo_url: h.repo_url,
      tool_name: p.tool_name,
      input: p.tool_input,
      output: p.tool_result,
    },
    summary: h.summary ?? null,
    timestamp: ts,
  };
}

function hookToEventStreamEntry(h: HookEvent, index: number): EventStreamEntry {
  const agentLog = hookToAgentLog(h);
  return {
    id: agentLog.id,
    lineNumber: index + 1,
    sourceType: "agent_log",
    level: hookEventTypeToLevel(h.hook_event_type),
    agentId: agentLog.agent_id,
    agentName: agentLog.agent_name,
    content: agentLog.content ?? "",
    timestamp: agentLog.timestamp,
    eventType: agentLog.event_type,
    eventCategory: agentLog.event_category,
    metadata: {
      originalEvent: agentLog,
      repo_url: h.repo_url,
      adw_id: h.adw_id,
      phase: h.phase,
      tool_name: (h.payload as any)?.tool_name,
    },
  };
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export const useOrchestratorStore = defineStore("orchestrator", () => {
  const pulse = useAgentPulse();

  // --- state ---
  const agents = ref<Agent[]>([]);
  const selectedAgentId = ref<string | null>(null);
  const orchestratorAgentId = ref<string>("tac-master");
  const orchestratorAgent = ref<OrchestratorAgent>({
    id: "tac-master",
    session_id: null,
    system_prompt: null,
    status: "executing",
    working_dir: "/srv/tac-master",
    input_tokens: 0,
    output_tokens: 0,
    total_cost: 0,
    archived: false,
    metadata: {},
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  });
  const eventStreamEntries = ref<EventStreamEntry[]>([]);
  const autoScroll = ref(true);
  const repos = ref<RepoStatus[]>([]);
  const isConnected = ref(false);
  const wsConnection = ref<WebSocket | null>(null);
  const websocketEventCount = ref(0);
  const commandInputVisible = ref(false);

  // --- view mode state (T118) ---
  /** Current view mode for the center column: 'logs' or 'adws' */
  const viewMode = ref<'logs' | 'adws'>('logs');

  // --- file tracking events (T118) ---
  /**
   * Map of event ID → file tracking data (changes + reads) received over WebSocket.
   * Components fall back to this when the event payload doesn't include file data.
   */
  const fileTrackingEvents = ref<Map<string, { file_changes?: any[]; read_files?: any[] }>>(new Map());

  // --- streaming block state (T076) ---
  const thinkingBlocks = ref<ThinkingBlockWsMessage["data"][]>([]);
  const toolUseBlocks = ref<ToolUseBlockWsMessage["data"][]>([]);
  const textBlocks = ref<Array<{
    id: string;
    orchestrator_agent_id: string;
    text: string;
    timestamp: number;
  }>>([]);
  const agentStatuses = ref<Array<{
    id: string;
    adw_id: string;
    session_id: string;
    phase: string;
    hook_event_type: string;
    timestamp: number;
  }>>([]);
  const lastHeartbeat = ref<number | null>(null);

  // --- getters ---
  const activeAgents = computed(() =>
    agents.value.filter((a) => !a.archived && a.status !== "complete"),
  );
  const runningAgents = computed(() =>
    agents.value.filter((a) => a.status === "executing"),
  );

  /**
   * All agents sorted by recency (running first, then by started_at desc).
   * Used by ActiveAgentsPanel so the panel is never empty — shows recent
   * agents even when nothing is currently running.
   */
  const recentAgents = computed<Agent[]>(() => {
    return [...agents.value].sort((a, b) => {
      // Running first
      const aRunning = a.status === "executing" ? 1 : 0;
      const bRunning = b.status === "executing" ? 1 : 0;
      if (bRunning !== aRunning) return bRunning - aRunning;
      // Then by started_at unix desc
      const aTs = (a.metadata?.started_at_unix as number | undefined) ?? 0;
      const bTs = (b.metadata?.started_at_unix as number | undefined) ?? 0;
      return bTs - aTs;
    });
  });

  const selectedAgent = computed(() =>
    agents.value.find((a) => a.id === selectedAgentId.value) ?? null,
  );

  // EventStream.vue reads this
  const filteredEventStream = computed(() => {
    if (!selectedAgentId.value) return eventStreamEntries.value;
    return eventStreamEntries.value.filter(
      (e) => e.agentId === selectedAgentId.value,
    );
  });

  const stats = computed<AppStats>(() => ({
    active: activeAgents.value.length,
    running: runningAgents.value.length,
    logs: eventStreamEntries.value.length,
    cost: repos.value.reduce((s, r) => s + r.cost_today_usd, 0),
  }));

  const totalTokensToday = computed(() =>
    repos.value.reduce((s, r) => s + r.tokens_today, 0),
  );

  /**
   * Running ADW workflows — agents with status 'executing' that have an adw_id.
   * Used by AppHeader to show the ADWS badge count.
   */
  const runningAdws = computed(() =>
    agents.value.filter((a) => a.status === 'executing' && a.adw_id),
  );

  /**
   * Get all streaming blocks for a given adw_id in timestamp order.
   * Returns a combined array of thinking, tool use, text, and agent status blocks
   * sorted by timestamp (ascending).
   */
  const activeBlocksByAdwId = (adwId: string) => {
    const blocks: Array<{
      type: "thinking" | "tool_use" | "text" | "agent_status";
      id: string;
      timestamp: number;
      data: any;
    }> = [];

    // Collect thinking blocks for this adw_id
    thinkingBlocks.value.forEach((block) => {
      if (block.orchestrator_agent_id === adwId) {
        blocks.push({
          type: "thinking",
          id: block.id,
          timestamp: block.timestamp,
          data: block,
        });
      }
    });

    // Collect tool use blocks for this adw_id
    toolUseBlocks.value.forEach((block) => {
      if (block.orchestrator_agent_id === adwId) {
        blocks.push({
          type: "tool_use",
          id: block.id,
          timestamp: block.timestamp,
          data: block,
        });
      }
    });

    // Collect text blocks for this adw_id
    textBlocks.value.forEach((block) => {
      if (block.orchestrator_agent_id === adwId) {
        blocks.push({
          type: "text",
          id: block.id,
          timestamp: block.timestamp,
          data: block,
        });
      }
    });

    // Collect agent status blocks for this adw_id
    agentStatuses.value.forEach((block) => {
      if (block.adw_id === adwId) {
        blocks.push({
          type: "agent_status",
          id: block.id,
          timestamp: block.timestamp,
          data: block,
        });
      }
    });

    // Sort by timestamp (ascending order)
    return blocks.sort((a, b) => a.timestamp - b.timestamp);
  };

  // --- actions ---

  function selectAgent(id: string | null) {
    selectedAgentId.value = id;
  }

  function toggleAutoScroll() {
    autoScroll.value = !autoScroll.value;
  }

  function clearEventStream() {
    eventStreamEntries.value = [];
  }

  function toggleCommandInput() {
    commandInputVisible.value = !commandInputVisible.value;
  }

  /** Set the center column view mode explicitly. */
  function setViewMode(mode: 'logs' | 'adws') {
    viewMode.value = mode;
  }

  /** Toggle the center column between 'logs' and 'adws'. */
  function toggleViewMode() {
    viewMode.value = viewMode.value === 'logs' ? 'adws' : 'logs';
  }

  function upsertRun(run: RunSummary) {
    const agent = runToAgent(run);
    const idx = agents.value.findIndex((a) => a.id === agent.id);
    if (idx === -1) {
      agents.value = [agent, ...agents.value].slice(0, 200);
    } else {
      agents.value[idx] = { ...agents.value[idx], ...agent };
    }
    pulse.triggerPulse(agent.id);
  }

  function upsertRepo(repo: RepoStatus) {
    const idx = repos.value.findIndex((r) => r.url === repo.url);
    if (idx === -1) {
      repos.value.push(repo);
    } else {
      repos.value[idx] = { ...repos.value[idx], ...repo };
    }
  }

  function addHookEvent(event: HookEvent) {
    const entry = hookToEventStreamEntry(event, eventStreamEntries.value.length);
    eventStreamEntries.value.push(entry);
    if (eventStreamEntries.value.length > MAX_EVENT_STREAM_SIZE) {
      eventStreamEntries.value.splice(
        0,
        eventStreamEntries.value.length - MAX_EVENT_STREAM_SIZE,
      );
    }
    if (entry.agentId) pulse.triggerPulse(entry.agentId);
  }

  function hydrateFromInitial(events: HookEvent[]) {
    eventStreamEntries.value = events.map((e, i) => hookToEventStreamEntry(e, i));
  }

  // --- streaming block actions (T076) ---

  /**
   * Add a thinking block to the thinking blocks array with FIFO eviction.
   * If array exceeds MAX_STREAMING_BLOCK_SIZE, remove oldest entry.
   */
  function addThinkingBlock(data: ThinkingBlockWsMessage["data"]) {
    thinkingBlocks.value.push(data);
    if (thinkingBlocks.value.length > MAX_STREAMING_BLOCK_SIZE) {
      thinkingBlocks.value.shift();
    }
  }

  /**
   * Add a tool use block to the tool use blocks array with FIFO eviction.
   * If array exceeds MAX_STREAMING_BLOCK_SIZE, remove oldest entry.
   */
  function addToolUseBlock(data: ToolUseBlockWsMessage["data"]) {
    toolUseBlocks.value.push(data);
    if (toolUseBlocks.value.length > MAX_STREAMING_BLOCK_SIZE) {
      toolUseBlocks.value.shift();
    }
  }

  /**
   * Add a text block to the text blocks array with FIFO eviction.
   * If array exceeds MAX_STREAMING_BLOCK_SIZE, remove oldest entry.
   */
  function addTextBlock(data: {
    id: string;
    orchestrator_agent_id: string;
    text: string;
    timestamp: number;
  }) {
    textBlocks.value.push(data);
    if (textBlocks.value.length > MAX_STREAMING_BLOCK_SIZE) {
      textBlocks.value.shift();
    }
  }

  /**
   * Add an agent status event to the agent statuses array with FIFO eviction.
   * If array exceeds MAX_STREAMING_BLOCK_SIZE, remove oldest entry.
   */
  function addAgentStatus(data: {
    id: string;
    adw_id: string;
    session_id: string;
    phase: string;
    hook_event_type: string;
    timestamp: number;
  }) {
    agentStatuses.value.push(data);
    if (agentStatuses.value.length > MAX_STREAMING_BLOCK_SIZE) {
      agentStatuses.value.shift();
    }
  }

  /**
   * Update the last heartbeat timestamp.
   */
  function updateHeartbeat(timestamp: number) {
    lastHeartbeat.value = timestamp;
  }

  // --- HTTP bootstrap ---

  async function loadInitial() {
    try {
      const [runsResp, reposResp, eventsResp] = await Promise.all([
        fetch("/api/runs?limit=50&sort=started_at:desc").then((r) => r.json()),
        fetch("/api/repos").then((r) => r.json()),
        fetch("/events/recent?limit=200").then((r) => r.json()),
      ]);
      agents.value = (runsResp.runs || []).map(runToAgent);
      repos.value = reposResp.repos || [];
      hydrateFromInitial(eventsResp || []);

      // Auto-select the most recent run so panels are never empty on mount.
      if (!selectedAgentId.value && recentAgents.value.length > 0) {
        selectedAgentId.value = recentAgents.value[0].id;
      }
    } catch (e) {
      console.error("[store] initial load failed:", e);
    }
  }

  // --- WebSocket ---

  function connectWebSocket() {
    const url =
      `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/stream`;
    const ws = new WebSocket(url);
    wsConnection.value = ws;

    ws.onopen = () => {
      isConnected.value = true;
    };

    ws.onmessage = (evt) => {
      websocketEventCount.value += 1;
      try {
        const msg = JSON.parse(evt.data) as TacWsMessage;
        switch (msg.type) {
          case "initial":
            hydrateFromInitial(msg.data);
            break;
          case "event":
            addHookEvent(msg.data);
            break;
          case "run_update":
            upsertRun(msg.data);
            break;
          case "repo_status":
            upsertRepo(msg.data);
            break;
        }
      } catch {
        // ignore parse errors
      }
    };

    ws.onclose = () => {
      isConnected.value = false;
      pulse.clearAllPulses();
      setTimeout(connectWebSocket, 3000);
    };
  }

  async function initialize() {
    await loadInitial();
    connectWebSocket();
  }

  return {
    // --- PUBLIC STATE ---
    /** Array of tac-master runs mapped to Agent shape */
    agents,
    /** Currently selected agent ID for filtering logs */
    selectedAgentId,
    /** Orchestrator agent ID (usually "tac-master") */
    orchestratorAgentId,
    /** Orchestrator agent metadata and cost tracking */
    orchestratorAgent,
    /** Event stream entries (hook events from all agents) */
    eventStreamEntries,
    /** Auto-scroll enabled flag for event stream */
    autoScroll,
    /** Repository status from /api/repos */
    repos,
    /** WebSocket connection state */
    isConnected,
    /** Count of WebSocket messages received */
    websocketEventCount,
    /** Command input visibility toggle */
    commandInputVisible,
    /** Current view mode for center column: 'logs' | 'adws' */
    viewMode,
    /** Map of event ID → file tracking data from WebSocket (real-time fallback) */
    fileTrackingEvents,

    // --- STREAMING BLOCK STATE (T076) ---
    /** Thinking blocks from streaming responses */
    thinkingBlocks,
    /** Tool use blocks from streaming responses */
    toolUseBlocks,
    /** Text blocks from streaming responses */
    textBlocks,
    /** Agent status events from hooks */
    agentStatuses,
    /** Timestamp of the last heartbeat event */
    lastHeartbeat,

    // --- PUBLIC GETTERS ---
    /** Active agents (not archived, status !== complete) */
    activeAgents,
    /** Agents with status === 'executing' */
    runningAgents,
    /** All agents sorted by recency (running first, then by started_at desc) */
    recentAgents,
    /** Currently selected agent or null */
    selectedAgent,
    /** Event stream filtered to selected agent or all if none selected */
    filteredEventStream,
    /** Computed stats: active, running, logs, cost */
    stats,
    /** Total tokens used across all repos today */
    totalTokensToday,
    /** Running ADW agents (executing + have adw_id) */
    runningAdws,
    /** Get all streaming blocks for a given adw_id in timestamp order */
    activeBlocksByAdwId,
    /** Check if an agent is currently pulsing (from useAgentPulse) */
    isAgentPulsing: pulse.isAgentPulsing,

    // --- PUBLIC ACTIONS ---
    /** Initialize store: load initial data and establish WebSocket */
    initialize,
    /** Load initial runs, repos, and recent events via HTTP */
    loadInitial,
    /** Select an agent by ID (null to deselect) */
    selectAgent,
    /** Toggle auto-scroll on event stream */
    toggleAutoScroll,
    /** Clear all event stream entries */
    clearEventStream,
    /** Toggle command input visibility */
    toggleCommandInput,
    /** Set the center column view mode */
    setViewMode,
    /** Toggle the center column between 'logs' and 'adws' */
    toggleViewMode,
    /** Add a hook event to the event stream */
    addHookEvent,
    /** Hydrate event stream from an initial batch of events */
    hydrateFromInitial,
    /** Upsert a run summary into the agents array */
    upsertRun,
    /** Upsert a repo status into the repos array */
    upsertRepo,

    // --- STREAMING BLOCK ACTIONS (T076) ---
    /** Add a thinking block to the store */
    addThinkingBlock,
    /** Add a tool use block to the store */
    addToolUseBlock,
    /** Add a text block to the store */
    addTextBlock,
    /** Add an agent status event to the store */
    addAgentStatus,
    /** Update the last heartbeat timestamp */
    updateHeartbeat,
  };
});
