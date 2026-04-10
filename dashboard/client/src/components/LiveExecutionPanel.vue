<template>
  <!--
    LiveExecutionPanel — Right sidebar showing the live execution feed for a
    selected agent run.

    Renders a chronological feed of:
      THINKING  (cyan/teal)  — Claude's reasoning text
      TOOL      (orange)     — Tool calls with human-readable summaries
      RESPONSE  (green)      — Claude's assistant text output
      HOOK      (yellow)     — Lifecycle hook events

    Data for THINKING/TOOL/RESPONSE comes from the /api/stream/:adw_id/:phase
    endpoint (parsed from raw_output.jsonl on LXC). HOOK events come from the
    orchestrator event store via /events/recent?adw_id=XXX.

    @task T050
    @epic T036
    @why T050: replace flat command chip list with PITER phase accordion so
         users can see the 8 pipeline phases, their status, and individual
         commands grouped beneath each phase.
  -->
  <aside class="live-execution-panel">
    <!-- Header -->
    <header class="lep-header">
      <span class="lep-title">LIVE EXECUTION</span>
      <span v-if="isLoading" class="lep-loading-dot" title="Loading stream..." />
      <span v-else-if="selectedAdwId" class="lep-connected-dot" />
    </header>

    <!-- Empty / no-selection state -->
    <div v-if="!selectedAdwId" class="lep-empty">
      <div class="lep-empty-icon">◈</div>
      <p>No agent runs found. Start a workflow to see live execution.</p>
    </div>

    <template v-else>
      <!-- PITER phase accordion -->
      <div class="lep-accordion">
        <div class="lep-accordion-title">PIPELINE PHASES</div>

        <div
          v-for="phase in piterPhases"
          :key="phase.key"
          class="lep-phase-group"
        >
          <!-- Phase row header -->
          <button
            class="lep-phase-row"
            :class="`phase-status-${phase.status}`"
            @click="togglePhase(phase.key)"
            :title="phase.label"
          >
            <span class="lep-phase-status-icon">{{ phaseStatusIcon(phase.status) }}</span>
            <span class="lep-phase-label">{{ phase.label }}</span>
            <span class="lep-phase-chevron">{{ expandedPhases.has(phase.key) ? '▾' : '▸' }}</span>
          </button>

          <!-- Phase children (commands) -->
          <div v-if="expandedPhases.has(phase.key)" class="lep-phase-children">
            <div v-if="phase.commands.length === 0" class="lep-no-commands">
              No commands recorded
            </div>
            <button
              v-for="(cmd, cmdIdx) in phase.commands"
              :key="cmd.raw"
              class="lep-cmd-row"
              :class="{ 'cmd-active': cmd.raw === activePhase }"
              :title="cmd.raw"
              @click="selectCommand(cmd.raw)"
            >
              <span class="lep-cmd-indent">├</span>
              <span class="lep-cmd-label">{{ cmd.display }}</span>
              <span
                v-if="cmdIdx === phase.commands.length - 1 && phase.commands.length > 1"
                class="lep-cmd-committer"
                title="Final committer step"
              >●</span>
            </button>
          </div>
        </div>

        <!-- Other (unmatched commands) -->
        <div v-if="otherCommands.length > 0" class="lep-phase-group">
          <button
            class="lep-phase-row phase-status-pending"
            @click="togglePhase('__other__')"
          >
            <span class="lep-phase-status-icon">○</span>
            <span class="lep-phase-label">Other</span>
            <span class="lep-phase-chevron">{{ expandedPhases.has('__other__') ? '▾' : '▸' }}</span>
          </button>
          <div v-if="expandedPhases.has('__other__')" class="lep-phase-children">
            <button
              v-for="cmd in otherCommands"
              :key="cmd.raw"
              class="lep-cmd-row"
              :class="{ 'cmd-active': cmd.raw === activePhase }"
              :title="cmd.raw"
              @click="selectCommand(cmd.raw)"
            >
              <span class="lep-cmd-indent">├</span>
              <span class="lep-cmd-label">{{ cmd.display }}</span>
            </button>
          </div>
        </div>
      </div>

      <!-- Filter bar (preserved from T048) -->
      <div class="filter-bar">
        <button
          :class="['filter-pill', 'filter-response', { active: filters.response }]"
          @click="toggleFilter('response')"
        >
          RESPONSE <span class="count">{{ counts.response }}</span>
        </button>
        <button
          :class="['filter-pill', 'filter-tool', { active: filters.tool }]"
          @click="toggleFilter('tool')"
        >
          TOOL <span class="count">{{ counts.tool }}</span>
        </button>
        <button
          :class="['filter-pill', 'filter-thinking', { active: filters.thinking }]"
          @click="toggleFilter('thinking')"
        >
          THINKING <span class="count">{{ counts.thinking }}</span>
        </button>
        <button
          :class="['filter-pill', 'filter-hook', { active: filters.hook }]"
          @click="toggleFilter('hook')"
        >
          HOOK <span class="count">{{ counts.hook }}</span>
        </button>
      </div>

      <!-- Error state -->
      <div v-if="error" class="lep-error">{{ error }}</div>

      <!-- Event feed -->
      <div class="lep-feed" ref="feedRef">
        <div v-if="filteredEvents.length === 0 && !isLoading" class="lep-empty-inline">
          No execution events captured for this run yet.
        </div>

        <template v-for="(ev, idx) in filteredEvents" :key="idx">
          <!-- THINKING card -->
          <div v-if="ev.type === 'thinking'" class="lep-card lep-thinking">
            <div class="lep-card-head">
              <span class="lep-ts">{{ fmtTs(ev.timestamp) }}</span>
              <span class="lep-badge badge-thinking">THINKING</span>
            </div>
            <div class="lep-content">
              <span v-if="!ev._expanded && ev.thinking && ev.thinking.length > COLLAPSE_CHARS">
                {{ ev.thinking.slice(0, COLLAPSE_CHARS) }}…
                <button class="lep-more-btn" @click="ev._expanded = true">Show More</button>
              </span>
              <span v-else>
                {{ ev.thinking }}
                <button
                  v-if="ev.thinking && ev.thinking.length > COLLAPSE_CHARS"
                  class="lep-more-btn"
                  @click="ev._expanded = false"
                >Show Less</button>
              </span>
            </div>
          </div>

          <!-- TOOL card -->
          <div v-else-if="ev.type === 'tool_use'" class="lep-card lep-tool">
            <div class="lep-card-head">
              <span class="lep-ts">{{ fmtTs(ev.timestamp) }}</span>
              <span class="lep-badge badge-tool">TOOL</span>
              <span class="lep-tool-name">{{ ev.tool_name }}</span>
            </div>
            <div class="lep-content lep-tool-summary">{{ ev.tool_summary }}</div>
          </div>

          <!-- RESPONSE card -->
          <div v-else-if="ev.type === 'response'" class="lep-card lep-response">
            <div class="lep-card-head">
              <span class="lep-ts">{{ fmtTs(ev.timestamp) }}</span>
              <span class="lep-badge badge-response">RESPONSE</span>
            </div>
            <div class="lep-content">
              <span v-if="!ev._expanded && ev.text && ev.text.length > COLLAPSE_CHARS">
                {{ ev.text.slice(0, COLLAPSE_CHARS) }}…
                <button class="lep-more-btn" @click="ev._expanded = true">Show More</button>
              </span>
              <span v-else>
                {{ ev.text }}
                <button
                  v-if="ev.text && ev.text.length > COLLAPSE_CHARS"
                  class="lep-more-btn"
                  @click="ev._expanded = false"
                >Show Less</button>
              </span>
            </div>
          </div>

          <!-- HOOK card -->
          <div v-else-if="ev.type === 'hook'" class="lep-card lep-hook">
            <div class="lep-card-head">
              <span class="lep-ts">{{ fmtTs(ev.timestamp) }}</span>
              <span class="lep-badge badge-hook">HOOK</span>
            </div>
            <div class="lep-content lep-hook-summary">{{ ev.hookSummary }}</div>
          </div>
        </template>

        <!-- Auto-scroll anchor -->
        <div ref="bottomRef" />
      </div>
    </template>
  </aside>
</template>

<script setup lang="ts">
/**
 * LiveExecutionPanel script.
 *
 * State management:
 * - Reads selectedAgent from orchestratorStore (same source as RunDetailsPanel)
 * - Fetches stream phases (commands) via GET /api/stream/:adw_id
 * - Fetches per-phase status via GET /api/runs/:adw_id/phases
 * - Fetches parsed events via GET /api/stream/:adw_id/:command
 * - Hook events fetched via GET /events/recent?adw_id=XXX (T048)
 *
 * T050: commands are now grouped into PITER phase accordion rows.
 * Clicking a phase expands it; clicking a command loads its events.
 * The filter bar (RESPONSE / TOOL / THINKING / HOOK) is preserved below.
 */

import { ref, computed, watch, nextTick, onMounted, reactive } from "vue";
import { useOrchestratorStore } from "../stores/orchestratorStore";
import type { EventStreamEntry } from "../types";

// ─── Types ─────────────────────────────────────────────────────────────────

interface StreamEvent {
  type: "thinking" | "tool_use" | "response" | "hook";
  timestamp: string | null;
  thinking?: string;
  text?: string;
  tool_name?: string;
  tool_summary?: string;
  hookSummary?: string;
  // UI state
  _expanded?: boolean;
}

interface CommandEntry {
  raw: string;
  display: string;
}

interface PiterPhaseEntry {
  key: string;
  label: string;
  color: string;
  patterns: string[];
  commands: CommandEntry[];
  status: "done" | "active" | "pending";
}

// ─── Constants ─────────────────────────────────────────────────────────────

/** Characters before we show "Show More" on thinking/response blocks */
const COLLAPSE_CHARS = 300;

/** Base API URL — matches the dashboard server */
const API_BASE = "";

/**
 * PITER phase definitions in canonical order.
 * patterns: substring match against command name (case-insensitive).
 */
const PHASE_DEFS: Array<{ key: string; label: string; color: string; patterns: string[] }> = [
  { key: "classify", label: "Classify", color: "#4488ff", patterns: ["issue_classifier", "classify"] },
  { key: "plan",     label: "Plan",     color: "#aa66ff", patterns: ["sdlc_planner"] },
  { key: "build",    label: "Build",    color: "#00ff66", patterns: ["sdlc_implementor", "branch_generator"] },
  { key: "test",     label: "Test",     color: "#ffcc00", patterns: ["test_runner", "test_resolver"] },
  { key: "review",   label: "Review",   color: "#ff8c00", patterns: ["reviewer"] },
  { key: "document", label: "Document", color: "#00cccc", patterns: ["documenter"] },
  { key: "ship",     label: "Ship",     color: "#00ffcc", patterns: ["pr_creator", "ops"] },
  { key: "reflect",  label: "Reflect",  color: "#888888", patterns: ["kpi_tracker"] },
];

// ─── Store ──────────────────────────────────────────────────────────────────

const store = useOrchestratorStore();

// The adw_id of the currently selected agent, falling back to most recent
const selectedAdwId = computed<string | null>(
  () => store.selectedAgent?.id ?? store.recentAgents[0]?.id ?? null,
);

// ─── Filter state ────────────────────────────────────────────────────────────

type FilterKey = "response" | "tool" | "thinking" | "hook";

const filters = reactive<Record<FilterKey, boolean>>({
  response: true,
  tool: true,
  thinking: true,
  hook: true,
});

function toggleFilter(key: FilterKey): void {
  filters[key] = !filters[key];
}

// ─── Command (phase) management ─────────────────────────────────────────────

/** Raw command names returned by /api/stream/:adw_id */
const availableCommands = ref<string[]>([]);

/** Currently selected command (used for event fetch) */
const activePhase = ref<string>("");

/** Which PITER phase accordion rows are expanded */
const expandedPhases = ref<Set<string>>(new Set());

function togglePhase(phaseKey: string): void {
  const s = new Set(expandedPhases.value);
  if (s.has(phaseKey)) {
    s.delete(phaseKey);
  } else {
    s.add(phaseKey);
  }
  expandedPhases.value = s;
}

async function fetchCommands(adwId: string): Promise<void> {
  try {
    const res = await fetch(`${API_BASE}/api/stream/${encodeURIComponent(adwId)}`);
    if (!res.ok) return;
    const data = (await res.json()) as { phases: string[] };
    availableCommands.value = data.phases ?? [];
    // Auto-select last command if none selected
    if (availableCommands.value.length > 0 && !activePhase.value) {
      activePhase.value = availableCommands.value[availableCommands.value.length - 1];
    }
  } catch {
    availableCommands.value = [];
  }
}

function selectCommand(rawName: string): void {
  activePhase.value = rawName;
}

// ─── Command name formatting ─────────────────────────────────────────────────

/**
 * Convert a raw command name to a human-readable display label.
 * - Strip _iter\d+_\d+ suffixes
 * - Replace underscores with spaces
 * - Title-case each word
 */
function formatCommandName(raw: string): string {
  const stripped = raw.replace(/_iter\d+_\d+$/, "");
  return stripped
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

// ─── PITER phase grouping ────────────────────────────────────────────────────

/**
 * Match a raw command name to its PITER phase key.
 * Returns null if no match.
 */
function matchPhase(cmdName: string): string | null {
  const lower = cmdName.toLowerCase();
  for (const def of PHASE_DEFS) {
    if (def.patterns.some((p) => lower.includes(p))) {
      return def.key;
    }
  }
  return null;
}

/** Commands that don't match any PITER phase */
const otherCommands = computed<CommandEntry[]>(() =>
  availableCommands.value
    .filter((c) => matchPhase(c) === null)
    .map((c) => ({ raw: c, display: formatCommandName(c) })),
);

// ─── Phase status from /api/runs/:adw_id/phases ──────────────────────────────

interface PhaseStatusEntry {
  phase: string;
  status: string;
}

const runPhaseStatuses = ref<PhaseStatusEntry[]>([]);

async function fetchRunPhases(adwId: string): Promise<void> {
  try {
    const res = await fetch(`${API_BASE}/api/runs/${encodeURIComponent(adwId)}/phases`);
    if (!res.ok) {
      runPhaseStatuses.value = [];
      return;
    }
    const data = (await res.json()) as { phases: PhaseStatusEntry[] };
    runPhaseStatuses.value = data.phases ?? [];
  } catch {
    runPhaseStatuses.value = [];
  }
}

/**
 * Map a PITER phase key to its run status.
 * Server uses keys like "classify_iso", "plan_iso", etc.
 * We normalise by stripping _iso suffix.
 */
function getPhaseStatus(phaseKey: string): "done" | "active" | "pending" {
  const entry = runPhaseStatuses.value.find((e) => {
    const norm = e.phase.replace(/_iso$/, "");
    return norm === phaseKey;
  });
  if (!entry) return "pending";
  const s = entry.status;
  if (s === "completed" || s === "succeeded" || s === "done") return "done";
  if (s === "active" || s === "running") return "active";
  return "pending";
}

// ─── Built PITER phase accordion data ───────────────────────────────────────

const piterPhases = computed<PiterPhaseEntry[]>(() => {
  // Build a map from phase key → commands
  const cmdsByPhase = new Map<string, CommandEntry[]>();
  for (const def of PHASE_DEFS) {
    cmdsByPhase.set(def.key, []);
  }
  for (const cmd of availableCommands.value) {
    const key = matchPhase(cmd);
    if (key && cmdsByPhase.has(key)) {
      cmdsByPhase.get(key)!.push({ raw: cmd, display: formatCommandName(cmd) });
    }
  }

  return PHASE_DEFS.map((def) => {
    const commands = cmdsByPhase.get(def.key) ?? [];
    let status: "done" | "active" | "pending";

    if (runPhaseStatuses.value.length > 0) {
      // Use server-provided status
      status = getPhaseStatus(def.key);
    } else {
      // Infer from command presence
      if (commands.length === 0) {
        status = "pending";
      } else {
        // Phase has commands — check if active command is in this phase
        const isActive = commands.some((c) => c.raw === activePhase.value);
        status = isActive ? "active" : "done";
      }
    }

    return {
      key: def.key,
      label: def.label,
      color: def.color,
      patterns: def.patterns,
      commands,
      status,
    };
  });
});

function phaseStatusIcon(status: "done" | "active" | "pending"): string {
  if (status === "done") return "✓";
  if (status === "active") return "→";
  return "○";
}

// ─── Stream events ──────────────────────────────────────────────────────────

const streamEvents = ref<StreamEvent[]>([]);
const isLoading = ref(false);
const error = ref<string | null>(null);

async function fetchStreamEvents(adwId: string, command: string): Promise<void> {
  if (!adwId || !command) return;
  isLoading.value = true;
  error.value = null;
  try {
    const url = `${API_BASE}/api/stream/${encodeURIComponent(adwId)}/${encodeURIComponent(command)}?limit=300`;
    const res = await fetch(url);
    if (!res.ok) {
      if (res.status === 404) {
        streamEvents.value = [];
        return;
      }
      throw new Error(`HTTP ${res.status}`);
    }
    const data = (await res.json()) as {
      events: Array<{
        type: "thinking" | "response" | "tool_use";
        timestamp: string | null;
        thinking?: string;
        text?: string;
        tool_name?: string;
        tool_summary?: string;
      }>;
    };
    streamEvents.value = (data.events ?? []).map((ev) => ({
      ...ev,
      _expanded: false,
    }));
  } catch (e: any) {
    error.value = String(e?.message ?? e);
    streamEvents.value = [];
  } finally {
    isLoading.value = false;
  }
}

// ─── Hook events from /events/recent?adw_id=XXX (T048) ─────────────────────

interface HookEventRow {
  id?: number;
  hook_event_type: string;
  payload: Record<string, unknown>;
  summary?: string;
  timestamp?: number;
  adw_id?: string;
}

const apiHookEvents = ref<StreamEvent[]>([]);

function summarizeHookEvent(ev: HookEventRow): string {
  const hookType = ev.hook_event_type ?? "unknown";
  const payload = ev.payload ?? {};

  if (hookType === "PreToolUse") {
    const toolName = (payload.tool_name as string) ?? "";
    return `About to run ${toolName || "tool"}`;
  }
  if (hookType === "PostToolUse") {
    const toolName = (payload.tool_name as string) ?? "";
    return `${toolName || "Tool"} completed`;
  }
  if (hookType === "UserPromptSubmit") {
    return "User prompt submitted";
  }
  if (hookType === "Stop") {
    const reason = (payload.reason as string) ?? "completed";
    return `Agent stopped (${reason})`;
  }
  if (hookType === "SubagentStop") {
    return "Subagent finished";
  }
  return ev.summary ?? hookType;
}

async function fetchApiHookEvents(adwId: string): Promise<void> {
  try {
    const url = `${API_BASE}/events/recent?adw_id=${encodeURIComponent(adwId)}&limit=200`;
    const res = await fetch(url);
    if (!res.ok) {
      apiHookEvents.value = [];
      return;
    }
    const rows = (await res.json()) as HookEventRow[];
    apiHookEvents.value = (rows ?? []).map((ev) => ({
      type: "hook" as const,
      timestamp: ev.timestamp ? new Date(ev.timestamp).toISOString() : null,
      hookSummary: summarizeHookEvent(ev),
      _expanded: false,
    }));
  } catch {
    apiHookEvents.value = [];
  }
}

// ─── Hook events from store (WebSocket live stream) ─────────────────────────

function hookEntryToStreamEvent(ev: EventStreamEntry): StreamEvent {
  const hookType = ev.eventType ?? "unknown";
  let summary = "";

  if (hookType === "PreToolUse" || hookType === "PostToolUse") {
    const toolName =
      (ev.metadata as any)?.tool_name ??
      (ev.metadata as any)?.payload?.tool_name ??
      "";
    const prefix = hookType === "PreToolUse" ? "About to run" : "Completed";
    summary = toolName ? `${prefix} ${toolName}` : hookType;
  } else if (hookType === "Stop") {
    summary = "Agent stopped.";
  } else if (hookType === "UserPromptSubmit") {
    summary = "User prompt submitted.";
  } else if (hookType === "SubagentStop") {
    summary = "Subagent finished.";
  } else {
    summary = ev.content?.slice(0, 120) ?? hookType;
  }

  return {
    type: "hook",
    timestamp: ev.timestamp ? new Date(ev.timestamp).toISOString() : null,
    hookSummary: summary,
    _expanded: false,
  };
}

const storeHookEvents = computed<EventStreamEntry[]>(() => {
  if (!selectedAdwId.value) return [];
  return store.eventStreamEntries.filter(
    (e) => e.agentId === selectedAdwId.value && e.eventCategory === "hook",
  );
});

// ─── Merged + sorted event feed ─────────────────────────────────────────────

const allEvents = computed<StreamEvent[]>(() => {
  const parsed: StreamEvent[] = streamEvents.value;
  const apiHooks: StreamEvent[] = apiHookEvents.value;
  const storeHooks: StreamEvent[] = storeHookEvents.value.map(hookEntryToStreamEvent);

  // Deduplicate store hooks vs api hooks by timestamp+type to avoid doubling
  // (store hooks arrive via WebSocket; api hooks come from DB — they overlap)
  const apiHookTimes = new Set(apiHooks.map((h) => h.timestamp));
  const uniqueStoreHooks = storeHooks.filter((h) => !apiHookTimes.has(h.timestamp));

  const combined = [...parsed, ...apiHooks, ...uniqueStoreHooks];

  // Sort by timestamp ascending; null timestamps go last
  combined.sort((a, b) => {
    if (!a.timestamp && !b.timestamp) return 0;
    if (!a.timestamp) return 1;
    if (!b.timestamp) return -1;
    return new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime();
  });

  return combined;
});

// ─── Event type counts ───────────────────────────────────────────────────────

const counts = computed(() => ({
  response: allEvents.value.filter((e) => e.type === "response").length,
  tool: allEvents.value.filter((e) => e.type === "tool_use").length,
  thinking: allEvents.value.filter((e) => e.type === "thinking").length,
  hook: allEvents.value.filter((e) => e.type === "hook").length,
}));

// ─── Filtered event list ─────────────────────────────────────────────────────

const filteredEvents = computed<StreamEvent[]>(() =>
  allEvents.value.filter((e) => {
    if (e.type === "response") return filters.response;
    if (e.type === "tool_use") return filters.tool;
    if (e.type === "thinking") return filters.thinking;
    if (e.type === "hook") return filters.hook;
    return true;
  }),
);

// ─── Auto-scroll ─────────────────────────────────────────────────────────────

const feedRef = ref<HTMLElement>();
const bottomRef = ref<HTMLElement>();

watch(
  () => filteredEvents.value.length,
  async () => {
    await nextTick();
    bottomRef.value?.scrollIntoView({ behavior: "smooth" });
  },
);

// ─── Timestamp formatter ────────────────────────────────────────────────────

function fmtTs(ts: string | null | undefined): string {
  if (!ts) return "——:——:——";
  try {
    const d = new Date(ts);
    return d.toLocaleTimeString("en-US", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  } catch {
    return "——:——:——";
  }
}

// ─── Watchers: react to agent selection and command changes ─────────────────

async function loadForAdwId(adwId: string): Promise<void> {
  streamEvents.value = [];
  apiHookEvents.value = [];
  availableCommands.value = [];
  runPhaseStatuses.value = [];
  activePhase.value = "";
  expandedPhases.value = new Set();
  error.value = null;

  // Fetch commands, phase statuses, and hook events concurrently
  await Promise.all([
    fetchCommands(adwId),
    fetchRunPhases(adwId),
    fetchApiHookEvents(adwId),
  ]);

  if (activePhase.value) {
    await fetchStreamEvents(adwId, activePhase.value);
  }
}

watch(selectedAdwId, async (adwId) => {
  if (!adwId) return;
  await loadForAdwId(adwId);
});

watch(activePhase, async (command) => {
  if (!selectedAdwId.value || !command) return;
  await fetchStreamEvents(selectedAdwId.value, command);
});

// On mount: if selectedAdwId is already populated (from store initial load),
// the watch above won't fire because the value was set before the watcher.
// Trigger load explicitly.
onMounted(async () => {
  const adwId = selectedAdwId.value;
  if (adwId) {
    await loadForAdwId(adwId);
  }
});
</script>

<style scoped>
.live-execution-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--color-bg-secondary, #111827);
  border-left: 1px solid var(--color-border, #1f2937);
  color: var(--color-text-primary, #e4e7eb);
  font-family: var(--font-mono, ui-monospace, "Cascadia Code", Menlo, monospace);
  overflow: hidden;
}

/* ─── Header ─────────────────────────────────────────── */
.lep-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  border-bottom: 1px solid #1f2937;
  background: #0d1117;
  flex-shrink: 0;
}

.lep-title {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: #a855f7;
}

.lep-connected-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #10b981;
  box-shadow: 0 0 6px #10b98180;
  margin-left: auto;
}

.lep-loading-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #f59e0b;
  margin-left: auto;
  animation: lep-pulse 1s ease-in-out infinite;
}

@keyframes lep-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}

/* ─── Phase accordion ────────────────────────────────── */
.lep-accordion {
  flex-shrink: 0;
  border-bottom: 1px solid #1f2937;
  background: #0d1117;
  overflow-y: auto;
  max-height: 280px;
}

.lep-accordion-title {
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: #4b5563;
  padding: 7px 12px 4px;
}

.lep-phase-group {
  /* Container for a single phase row + its children */
}

.lep-phase-row {
  display: flex;
  align-items: center;
  gap: 7px;
  width: 100%;
  padding: 4px 12px;
  background: transparent;
  border: none;
  font-family: inherit;
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  text-align: left;
  letter-spacing: 0.03em;
  transition: background 0.1s;
  color: #6b7280;
}

.lep-phase-row:hover {
  background: rgba(255, 255, 255, 0.03);
  color: #9ca3af;
}

/* Status-specific colors */
.phase-status-done .lep-phase-status-icon {
  color: #10b981;
}

.phase-status-done .lep-phase-label {
  color: #d1d5db;
}

.phase-status-active .lep-phase-status-icon {
  color: #22d3ee;
  animation: lep-pulse 1.2s ease-in-out infinite;
}

.phase-status-active .lep-phase-label {
  color: #22d3ee;
}

.phase-status-pending .lep-phase-status-icon {
  color: #374151;
}

.phase-status-pending .lep-phase-label {
  color: #4b5563;
}

.lep-phase-status-icon {
  width: 14px;
  flex-shrink: 0;
  font-size: 11px;
  text-align: center;
}

.lep-phase-label {
  flex: 1;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.lep-phase-chevron {
  font-size: 10px;
  color: #374151;
  flex-shrink: 0;
}

/* ─── Phase children (commands) ──────────────────────── */
.lep-phase-children {
  padding-bottom: 2px;
}

.lep-no-commands {
  padding: 2px 12px 2px 30px;
  font-size: 10px;
  color: #374151;
  font-style: italic;
}

.lep-cmd-row {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 3px 12px 3px 24px;
  background: transparent;
  border: none;
  font-family: inherit;
  font-size: 10px;
  cursor: pointer;
  text-align: left;
  color: #6b7280;
  transition: background 0.1s, color 0.1s;
}

.lep-cmd-row:hover {
  background: rgba(255, 255, 255, 0.04);
  color: #9ca3af;
}

.lep-cmd-row.cmd-active {
  color: #22d3ee;
  background: rgba(34, 211, 238, 0.06);
}

.lep-cmd-indent {
  color: #374151;
  font-size: 10px;
  flex-shrink: 0;
}

.lep-cmd-label {
  flex: 1;
  font-size: 10px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.lep-cmd-committer {
  font-size: 8px;
  color: #a855f7;
  flex-shrink: 0;
  margin-left: 2px;
  opacity: 0.8;
}

/* ─── Filter bar ─────────────────────────────────────── */
.filter-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  padding: 6px 10px;
  border-bottom: 1px solid #1f2937;
  background: #0d1117;
  flex-shrink: 0;
}

.filter-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: transparent;
  border: 1px solid #2d3748;
  border-radius: 3px;
  padding: 2px 7px;
  font-size: 9px;
  font-family: inherit;
  font-weight: 700;
  letter-spacing: 0.07em;
  color: #4b5563;
  cursor: pointer;
  transition: all 0.12s;
  text-transform: uppercase;
}

.filter-pill .count {
  font-size: 9px;
  font-weight: 600;
  opacity: 0.8;
}

/* RESPONSE — green */
.filter-pill.filter-response.active {
  border-color: #00ff66;
  color: #00ff66;
  background: rgba(0, 255, 102, 0.08);
}

.filter-pill.filter-response:not(.active):hover {
  border-color: #4b5563;
  color: #6b7280;
}

/* TOOL — orange */
.filter-pill.filter-tool.active {
  border-color: #ff8c00;
  color: #ff8c00;
  background: rgba(255, 140, 0, 0.08);
}

.filter-pill.filter-tool:not(.active):hover {
  border-color: #4b5563;
  color: #6b7280;
}

/* THINKING — cyan */
.filter-pill.filter-thinking.active {
  border-color: #00ffcc;
  color: #00ffcc;
  background: rgba(0, 255, 204, 0.08);
}

.filter-pill.filter-thinking:not(.active):hover {
  border-color: #4b5563;
  color: #6b7280;
}

/* HOOK — yellow */
.filter-pill.filter-hook.active {
  border-color: #ffcc00;
  color: #ffcc00;
  background: rgba(255, 204, 0, 0.08);
}

.filter-pill.filter-hook:not(.active):hover {
  border-color: #4b5563;
  color: #6b7280;
}

/* ─── Error ───────────────────────────────────────────── */
.lep-error {
  padding: 8px 16px;
  font-size: 11px;
  color: #f87171;
  background: #1c0606;
  border-bottom: 1px solid #7f1d1d;
  flex-shrink: 0;
}

/* ─── Empty states ────────────────────────────────────── */
.lep-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 40px 24px;
  color: #4b5563;
}

.lep-empty-icon {
  font-size: 32px;
  margin-bottom: 12px;
  opacity: 0.3;
}

.lep-empty p {
  font-size: 11px;
  line-height: 1.5;
  margin: 0;
}

.lep-empty-inline {
  padding: 16px;
  font-size: 11px;
  color: #4b5563;
  font-style: italic;
}

/* ─── Feed ────────────────────────────────────────────── */
.lep-feed {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.lep-feed::-webkit-scrollbar {
  width: 4px;
}

.lep-feed::-webkit-scrollbar-thumb {
  background: #2d3748;
  border-radius: 2px;
}

/* ─── Event cards ─────────────────────────────────────── */
.lep-card {
  border-radius: 4px;
  border: 1px solid transparent;
  padding: 7px 10px;
  font-size: 11px;
  line-height: 1.5;
}

.lep-card-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 5px;
  flex-wrap: wrap;
}

.lep-ts {
  font-size: 10px;
  color: #374151;
  white-space: nowrap;
  letter-spacing: 0.03em;
  font-family: inherit;
}

.lep-badge {
  display: inline-block;
  font-size: 9px;
  font-weight: 800;
  letter-spacing: 0.08em;
  padding: 1px 5px;
  border-radius: 3px;
  white-space: nowrap;
  text-transform: uppercase;
}

/* THINKING — cyan/teal */
.lep-thinking {
  background: rgba(6, 182, 212, 0.04);
  border-color: rgba(6, 182, 212, 0.15);
}

.badge-thinking {
  background: rgba(6, 182, 212, 0.15);
  color: #22d3ee;
  border: 1px solid rgba(6, 182, 212, 0.3);
}

/* TOOL — orange */
.lep-tool {
  background: rgba(249, 115, 22, 0.04);
  border-color: rgba(249, 115, 22, 0.15);
}

.badge-tool {
  background: rgba(249, 115, 22, 0.15);
  color: #fb923c;
  border: 1px solid rgba(249, 115, 22, 0.3);
}

.lep-tool-name {
  font-size: 10px;
  font-weight: 700;
  color: #fdba74;
}

/* RESPONSE — green */
.lep-response {
  background: rgba(16, 185, 129, 0.04);
  border-color: rgba(16, 185, 129, 0.15);
}

.badge-response {
  background: rgba(16, 185, 129, 0.15);
  color: #34d399;
  border: 1px solid rgba(16, 185, 129, 0.3);
}

/* HOOK — yellow */
.lep-hook {
  background: rgba(245, 158, 11, 0.04);
  border-color: rgba(245, 158, 11, 0.15);
}

.badge-hook {
  background: rgba(245, 158, 11, 0.15);
  color: #fbbf24;
  border: 1px solid rgba(245, 158, 11, 0.3);
}

/* ─── Content ─────────────────────────────────────────── */
.lep-content {
  color: #9ca3af;
  word-break: break-word;
  white-space: pre-wrap;
}

.lep-tool-summary {
  color: #d1d5db;
  font-size: 11px;
  font-style: normal;
  white-space: pre-wrap;
}

.lep-hook-summary {
  color: #d1d5db;
}

/* ─── Show More / Less button ──────────────────────────── */
.lep-more-btn {
  display: inline;
  background: transparent;
  border: none;
  color: #6366f1;
  font-size: 10px;
  font-family: inherit;
  cursor: pointer;
  padding: 0 4px;
  text-decoration: underline;
  white-space: nowrap;
}

.lep-more-btn:hover {
  color: #818cf8;
}
</style>
