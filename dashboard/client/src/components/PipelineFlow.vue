<template>
  <div class="pipeline-flow">
    <div class="panel-header">
      <span class="panel-title">PIPELINE FLOW</span>
      <span class="live-badge">
        <span class="live-dot" />
        LIVE EXECUTION
      </span>
    </div>

    <div class="pipeline-body">
      <!-- Phase bar -->
      <div class="phase-bar">
        <template v-for="(phase, idx) in phases" :key="phase.key">
          <div
            class="phase-node"
            :class="[phaseClass(phase.key), { 'phase-clickable': !!selectedRun }]"
            :title="selectedRun ? `Click to view ${phase.label} details` : phase.label"
            @click="onPhaseClick(phase.key)"
          >
            <div class="phase-icon-wrap">
              <!-- completed -->
              <svg
                v-if="phaseStatus(phase.key) === 'completed'"
                class="phase-icon icon-check"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2.5"
              >
                <polyline points="20 6 9 17 4 12" />
              </svg>
              <!-- failed -->
              <svg
                v-else-if="phaseStatus(phase.key) === 'failed'"
                class="phase-icon icon-x"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2.5"
              >
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
              <!-- active: pulsing ring -->
              <span v-else-if="phaseStatus(phase.key) === 'active'" class="phase-active-ring">
                <span class="phase-active-dot" />
              </span>
              <!-- pending: empty circle -->
              <span v-else class="phase-pending-circle" />
            </div>
            <span class="phase-label">{{ phase.label }}</span>
          </div>

          <!-- Connector line (not after last) -->
          <div
            v-if="idx < phases.length - 1"
            class="phase-connector"
            :class="connectorClass(phase.key)"
          />
        </template>
      </div>

      <!-- Dependency graph (T054) -->
      <div class="dep-graph-area">
        <DependencyGraph :run="selectedRun" />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * PipelineFlow — horizontal PITER phase bar for the Command Center.
 *
 * Takes an optional `run` prop (RunSummary + phase data). When no run is
 * provided it falls back to the store's selectedAgent. Phase statuses are
 * derived from the /api/runs/:adw_id/phases endpoint (fetched on run change)
 * and kept live via WebSocket event.phase updates.
 *
 * @task T038
 * @epic T036
 */
import { computed, ref, watch, onMounted } from "vue";
import { useOrchestratorStore } from "../stores/orchestratorStore";
import type { RunSummary } from "../types";
import DependencyGraph from "./DependencyGraph.vue";

const props = withDefaults(
  defineProps<{
    run?: RunSummary | null;
  }>(),
  { run: null },
);

const emit = defineEmits<{
  (e: "phase-click", payload: { adwId: string; phase: string }): void;
}>();

const store = useOrchestratorStore();

// PITER phases in canonical order
const phases = [
  { key: "classify_iso", label: "Classify" },
  { key: "plan_iso",     label: "Plan" },
  { key: "build_iso",    label: "Build" },
  { key: "test_iso",     label: "Test" },
  { key: "review_iso",   label: "Review" },
  { key: "document_iso", label: "Document" },
  { key: "ship_iso",     label: "Ship" },
  { key: "reflect_iso",  label: "Reflect" },
] as const;

type PhaseKey = typeof phases[number]["key"];
type PhaseStatus = "completed" | "active" | "pending" | "failed";

// Phase data fetched from server (map phase_key → status string)
const phaseData = ref<Record<string, string>>({});
const loadingPhases = ref(false);

// Determine which run to display: prop > selected store agent > most recent agent
const selectedRun = computed<RunSummary | null>(() => {
  if (props.run) return props.run;
  // Fall back to the most recently selected or most recent known agent
  const agent = store.selectedAgent ?? store.recentAgents[0] ?? null;
  if (!agent) return null;
  // Reconstruct a minimal RunSummary from the agent shape
  const meta = agent.metadata ?? {};
  return {
    adw_id: agent.id,
    repo_url: (meta.repo_url as string) ?? "",
    issue_number: (meta.issue_number as number) ?? 0,
    workflow: (agent.adw_step as string) ?? "",
    model_set: "",
    status: (meta.run_status as string) ?? agent.status,
    tokens_used: agent.input_tokens + agent.output_tokens,
  } as RunSummary;
});

// Current active phase: derived from event stream phase labels for the run
const activePhaseKey = computed<string | null>(() => {
  if (!selectedRun.value) return null;
  const runId = selectedRun.value.adw_id;
  const events = store.eventStreamEntries.filter(
    (e) => e.agentId === runId && e.metadata?.phase,
  );
  if (events.length === 0) return null;
  const last = events[events.length - 1];
  return normalisePhaseKey(String(last.metadata!.phase));
});

function normalisePhaseKey(raw: string): string {
  // raw values from events may be "classify_iso", "plan_iso", or plain "classify"
  if (raw.endsWith("_iso")) return raw;
  return `${raw}_iso`;
}

// Fetch phase breakdown from server endpoint
async function fetchPhases(adwId: string): Promise<void> {
  loadingPhases.value = true;
  try {
    const resp = await fetch(`/api/runs/${encodeURIComponent(adwId)}/phases`);
    if (!resp.ok) return;
    const data = (await resp.json()) as { phases: Array<{ phase: string; status: string }> };
    const map: Record<string, string> = {};
    for (const p of data.phases ?? []) {
      map[normalisePhaseKey(p.phase)] = p.status;
    }
    phaseData.value = map;
  } catch {
    // silently ignore — stale data is acceptable
  } finally {
    loadingPhases.value = false;
  }
}

watch(
  selectedRun,
  (run) => {
    phaseData.value = {};
    if (run?.adw_id) fetchPhases(run.adw_id);
  },
  { immediate: true },
);

// Map a phase key to its display status
function phaseStatus(key: string): PhaseStatus {
  // Check server-side phase data first
  const serverStatus = phaseData.value[key];
  if (serverStatus === "succeeded" || serverStatus === "completed") return "completed";
  if (serverStatus === "failed" || serverStatus === "aborted") return "failed";
  if (serverStatus === "running") return "active";

  // Fall back to event-stream position
  if (!selectedRun.value) return "pending";
  const active = activePhaseKey.value;
  if (!active) return "pending";

  const phaseKeys = phases.map((p) => p.key as string);
  const activeIdx = phaseKeys.indexOf(active);
  const thisIdx = phaseKeys.indexOf(key);
  if (thisIdx < 0) return "pending";
  if (thisIdx < activeIdx) return "completed";
  if (thisIdx === activeIdx) return "active";
  return "pending";
}

function phaseClass(key: string): string {
  return `phase-${phaseStatus(key)}`;
}

function connectorClass(key: string): string {
  const s = phaseStatus(key);
  return s === "completed" ? "connector-completed" : "connector-pending";
}

function onPhaseClick(phaseKey: string): void {
  if (!selectedRun.value) return;
  emit("phase-click", { adwId: selectedRun.value.adw_id, phase: phaseKey });
}
</script>

<style scoped>
.pipeline-flow {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: #0d0f1a;
  border: 1px solid rgba(255, 255, 255, 0.07);
  border-radius: 6px;
  overflow: hidden;
  font-family: var(--font-mono, ui-monospace, monospace);
}

/* ── Header ── */
.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  background: #12171e;
  border-bottom: 1px solid rgba(255, 255, 255, 0.07);
  flex-shrink: 0;
}

.panel-title {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.12em;
  color: #6b7280;
  text-transform: uppercase;
}

.live-badge {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: #3b82f6;
  background: rgba(59, 130, 246, 0.1);
  border: 1px solid rgba(59, 130, 246, 0.25);
  border-radius: 4px;
  padding: 2px 8px;
}

.live-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #3b82f6;
  animation: liveBlink 1s steps(1) infinite;
}

@keyframes liveBlink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}

/* ── Body ── */
.pipeline-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 12px 16px;
  gap: 12px;
  overflow: hidden;
}

/* ── Phase bar ── */
.phase-bar {
  display: flex;
  align-items: center;
  gap: 0;
  overflow-x: auto;
  padding: 4px 0 8px;
}

.phase-bar::-webkit-scrollbar {
  height: 3px;
}
.phase-bar::-webkit-scrollbar-track {
  background: transparent;
}
.phase-bar::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.1);
  border-radius: 2px;
}

/* ── Phase node ── */
.phase-node {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 5px;
  flex-shrink: 0;
  min-width: 64px;
}

.phase-clickable {
  cursor: pointer;
}
.phase-clickable:hover .phase-icon-wrap {
  opacity: 0.85;
  transform: scale(1.08);
}
.phase-clickable:hover .phase-label {
  opacity: 0.85;
}

.phase-icon-wrap {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  border: 2px solid;
  position: relative;
  transition: all 0.25s;
}

/* Completed */
.phase-completed .phase-icon-wrap {
  border-color: #10b981;
  background: rgba(16, 185, 129, 0.15);
}
.icon-check {
  width: 14px;
  height: 14px;
  color: #10b981;
}

/* Failed */
.phase-failed .phase-icon-wrap {
  border-color: #ef4444;
  background: rgba(239, 68, 68, 0.15);
}
.icon-x {
  width: 14px;
  height: 14px;
  color: #ef4444;
}

/* Active */
.phase-active .phase-icon-wrap {
  border-color: #10b981;
  background: rgba(16, 185, 129, 0.12);
  box-shadow: 0 0 0 3px rgba(16, 185, 129, 0.2);
  animation: activeGlow 1.5s ease-in-out infinite;
}

@keyframes activeGlow {
  0%, 100% {
    box-shadow: 0 0 0 2px rgba(16, 185, 129, 0.15), 0 0 8px rgba(16, 185, 129, 0.2);
  }
  50% {
    box-shadow: 0 0 0 4px rgba(16, 185, 129, 0.25), 0 0 14px rgba(16, 185, 129, 0.35);
  }
}

.phase-active-ring {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
}

.phase-active-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #10b981;
}

/* Pending */
.phase-pending .phase-icon-wrap {
  border-color: rgba(255, 255, 255, 0.15);
  background: transparent;
}

.phase-pending-circle {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  border: 1.5px solid rgba(255, 255, 255, 0.2);
}

/* Phase label */
.phase-label {
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  white-space: nowrap;
}

.phase-completed .phase-label { color: #10b981; }
.phase-active .phase-label    { color: #10b981; }
.phase-failed .phase-label    { color: #ef4444; }
.phase-pending .phase-label   { color: #4b5563; }

/* ── Connector line ── */
.phase-connector {
  flex: 1;
  height: 2px;
  margin-bottom: 18px; /* align with icon center */
  min-width: 12px;
}

.connector-completed {
  background: linear-gradient(90deg, #10b981, #10b981);
}

.connector-pending {
  background: rgba(255, 255, 255, 0.08);
}

/* ── Dependency graph area ── */
.dep-graph-area {
  flex: 1;
  border: 1px dashed rgba(255, 255, 255, 0.08);
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 48px;
  background: rgba(0, 0, 0, 0.15);
}

/* DependencyGraph fills the dep-graph-area */
</style>
