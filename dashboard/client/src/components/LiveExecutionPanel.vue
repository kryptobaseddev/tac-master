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
    orchestrator event store.

    @task T039
    @epic T036
    @why The existing RunDetailsPanel showed raw hook events only. Thinking and
         response content lives in raw_output.jsonl and was never surfaced.
    @what Full live execution feed with color-coded compact event cards,
          "Show More" for long thinking blocks, and human-readable tool display.
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
      <p>Select an agent run to view its execution stream.</p>
    </div>

    <!-- Phase selector when multiple phases exist -->
    <div v-else-if="availablePhases.length > 1" class="lep-phase-bar">
      <button
        v-for="ph in availablePhases"
        :key="ph"
        class="lep-phase-btn"
        :class="{ active: ph === activePhase }"
        @click="selectPhase(ph)"
      >{{ ph }}</button>
    </div>

    <!-- Error state -->
    <div v-if="error" class="lep-error">{{ error }}</div>

    <!-- Event feed -->
    <div class="lep-feed" ref="feedRef">
      <div v-if="selectedAdwId && allEvents.length === 0 && !isLoading" class="lep-empty-inline">
        No execution events captured for this run yet.
      </div>

      <template v-for="(ev, idx) in allEvents" :key="idx">
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
  </aside>
</template>

<script setup lang="ts">
/**
 * LiveExecutionPanel script.
 *
 * State management:
 * - Reads selectedAgent from orchestratorStore (same source as RunDetailsPanel)
 * - Fetches stream phases via GET /api/stream/:adw_id
 * - Fetches parsed events via GET /api/stream/:adw_id/:phase
 * - Hook events come from store.eventStreamEntries filtered by agentId
 *
 * Events are merged chronologically by timestamp.
 */

import { ref, computed, watch, nextTick } from "vue";
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

// ─── Constants ─────────────────────────────────────────────────────────────

/** Characters before we show "Show More" on thinking/response blocks */
const COLLAPSE_CHARS = 300;

/** Base API URL — matches the dashboard server */
const API_BASE = "";

// ─── Store ──────────────────────────────────────────────────────────────────

const store = useOrchestratorStore();

// The adw_id of the currently selected agent
const selectedAdwId = computed<string | null>(() => store.selectedAgent?.id ?? null);

// Hook events for the selected agent (from existing event stream)
const hookEvents = computed<EventStreamEntry[]>(() => {
  if (!selectedAdwId.value) return [];
  return store.eventStreamEntries.filter(
    (e) => e.agentId === selectedAdwId.value && e.eventCategory === "hook",
  );
});

// ─── Phase management ───────────────────────────────────────────────────────

const availablePhases = ref<string[]>([]);
const activePhase = ref<string>("");

async function fetchPhases(adwId: string): Promise<void> {
  try {
    const res = await fetch(`${API_BASE}/api/stream/${encodeURIComponent(adwId)}`);
    if (!res.ok) return;
    const data = (await res.json()) as { phases: string[] };
    availablePhases.value = data.phases ?? [];
    if (availablePhases.value.length > 0 && !activePhase.value) {
      activePhase.value = availablePhases.value[availablePhases.value.length - 1];
    }
  } catch {
    availablePhases.value = [];
  }
}

function selectPhase(phase: string): void {
  activePhase.value = phase;
}

// ─── Stream events ──────────────────────────────────────────────────────────

const streamEvents = ref<StreamEvent[]>([]);
const isLoading = ref(false);
const error = ref<string | null>(null);

async function fetchStreamEvents(adwId: string, phase: string): Promise<void> {
  if (!adwId || !phase) return;
  isLoading.value = true;
  error.value = null;
  try {
    const url = `${API_BASE}/api/stream/${encodeURIComponent(adwId)}/${encodeURIComponent(phase)}?limit=300`;
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

// ─── Hook events → StreamEvent ──────────────────────────────────────────────

function hookEventToStreamEvent(ev: EventStreamEntry): StreamEvent {
  const hookType = ev.eventType ?? "unknown";
  // Build a one-line human-readable summary
  let summary = "";

  // PreToolUse / PostToolUse
  if (hookType === "PreToolUse" || hookType === "PostToolUse") {
    const toolName = (ev.metadata as any)?.tool_name ?? (ev.metadata as any)?.payload?.tool_name ?? "";
    const prefix = hookType === "PreToolUse" ? "Before" : "After";
    summary = toolName ? `${prefix} ${toolName}` : hookType;
  }
  // Stop
  else if (hookType === "Stop") {
    summary = "Agent stopped.";
  }
  // UserPromptSubmit
  else if (hookType === "UserPromptSubmit") {
    summary = "User prompt submitted.";
  }
  // Generic fallback
  else {
    summary = ev.content?.slice(0, 120) ?? hookType;
  }

  return {
    type: "hook",
    timestamp: ev.timestamp ? new Date(ev.timestamp).toISOString() : null,
    hookSummary: summary,
    _expanded: false,
  };
}

// ─── Merged + sorted event feed ─────────────────────────────────────────────

const allEvents = computed<StreamEvent[]>(() => {
  const parsed: StreamEvent[] = streamEvents.value;
  const hooks: StreamEvent[] = hookEvents.value.map(hookEventToStreamEvent);

  const combined = [...parsed, ...hooks];

  // Sort by timestamp ascending; null timestamps go last
  combined.sort((a, b) => {
    if (!a.timestamp && !b.timestamp) return 0;
    if (!a.timestamp) return 1;
    if (!b.timestamp) return -1;
    return new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime();
  });

  return combined;
});

// ─── Auto-scroll ─────────────────────────────────────────────────────────────

const feedRef = ref<HTMLElement>();
const bottomRef = ref<HTMLElement>();

watch(
  () => allEvents.value.length,
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

// ─── Watchers: react to agent selection and phase changes ───────────────────

watch(selectedAdwId, async (adwId) => {
  streamEvents.value = [];
  availablePhases.value = [];
  activePhase.value = "";
  error.value = null;

  if (!adwId) return;

  await fetchPhases(adwId);
  if (activePhase.value) {
    await fetchStreamEvents(adwId, activePhase.value);
  }
});

watch(activePhase, async (phase) => {
  if (!selectedAdwId.value || !phase) return;
  await fetchStreamEvents(selectedAdwId.value, phase);
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

/* ─── Phase bar ──────────────────────────────────────── */
.lep-phase-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  padding: 8px 12px;
  border-bottom: 1px solid #1f2937;
  background: #0d1117;
  flex-shrink: 0;
}

.lep-phase-btn {
  background: transparent;
  border: 1px solid #2d3748;
  border-radius: 3px;
  padding: 3px 8px;
  font-size: 10px;
  font-family: inherit;
  font-weight: 600;
  color: #6b7280;
  cursor: pointer;
  letter-spacing: 0.03em;
  transition: all 0.12s;
}

.lep-phase-btn:hover {
  color: #d1d5db;
  border-color: #4b5563;
}

.lep-phase-btn.active {
  background: rgba(168, 85, 247, 0.12);
  border-color: #a855f7;
  color: #c084fc;
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
