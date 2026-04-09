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
} from "../types";

const MAX_EVENT_STREAM_SIZE = 2000;

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
    input_tokens: 0,
    output_tokens: run.tokens_used,
    total_cost: 0, // populated by token ledger via separate endpoint later
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
    },
    task: `${slug} · issue #${run.issue_number}`,
    log_count: 0,
    latest_summary: `${run.workflow} → ${run.status}`,
    created_at: started,
    updated_at: ended,
  };
}

function hookEventTypeToCategory(t: string): EventCategory {
  // Claude Code hook events are all "hook" category in the orchestrator taxonomy
  return "hook";
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
    event_type: h.hook_event_type === "PreToolUse" || h.hook_event_type === "PostToolUse"
      ? "tool_use"
      : h.hook_event_type === "UserPromptSubmit"
        ? "text"
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

  // --- getters ---
  const activeAgents = computed(() =>
    agents.value.filter((a) => !a.archived && a.status !== "complete"),
  );
  const runningAgents = computed(() =>
    agents.value.filter((a) => a.status === "executing"),
  );
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

  // --- HTTP bootstrap ---

  async function loadInitial() {
    try {
      const [runsResp, reposResp, eventsResp] = await Promise.all([
        fetch("/api/runs").then((r) => r.json()),
        fetch("/api/repos").then((r) => r.json()),
        fetch("/events/recent?limit=200").then((r) => r.json()),
      ]);
      agents.value = (runsResp.runs || []).map(runToAgent);
      repos.value = reposResp.repos || [];
      hydrateFromInitial(eventsResp || []);
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
    // state
    agents,
    selectedAgentId,
    orchestratorAgent,
    orchestratorAgentId,
    eventStreamEntries,
    autoScroll,
    repos,
    isConnected,
    websocketEventCount,

    // getters
    activeAgents,
    runningAgents,
    selectedAgent,
    filteredEventStream,
    stats,
    totalTokensToday,

    // actions
    initialize,
    selectAgent,
    toggleAutoScroll,
    clearEventStream,
    upsertRun,
    upsertRepo,
    addHookEvent,
  };
});
