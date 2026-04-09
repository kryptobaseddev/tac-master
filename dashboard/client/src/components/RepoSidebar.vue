<script setup lang="ts">
/**
 * RepoSidebar — Left sidebar showing TAC-MASTER metrics for the selected repo.
 *
 * Displays aggregate task counts from CLEO (total / in-progress / completed /
 * failed+blocked / pending), escalation items (blocked or critical+not-done
 * tasks), and quick-links to System Logs and Docs.
 *
 * Data pulled from:
 *   /api/cleo/stats   — aggregate counts across all epics
 *   /api/cleo/epics   — used to derive escalation items
 *
 * Auto-refreshes every 30 seconds.
 *
 * Props:
 *   selectedRepoUrl — URL of the currently selected repo (passed through)
 *   activeTab       — current app tab
 *
 * Emits:
 *   select-repo  — forwarded repo selection
 *   navigate     — tab string when a bottom link is clicked
 *   open-logs    — when System Logs link is clicked
 *
 * @task T043
 * @epic T042
 */

import { ref, onMounted, onUnmounted } from "vue";
import type { EpicSummary, TaskSummary } from "../stores/cleoStore";

const props = defineProps<{
  selectedRepoUrl: string;
  activeTab: string;
}>();

const emit = defineEmits<{
  (e: "select-repo", url: string): void;
  (e: "navigate", tab: string): void;
  (e: "open-logs"): void;
}>();

// ── Stats ─────────────────────────────────────────────────────────

interface CleoStats {
  total: number;
  done: number;
  active: number;
  pending: number;
  blocked: number;
}

const stats = ref<CleoStats>({ total: 0, done: 0, active: 0, pending: 0, blocked: 0 });
const loading = ref(false);
const error = ref<string | null>(null);

// ── Escalations ────────────────────────────────────────────────────

interface Escalation {
  id: string;
  title: string;
  priority: string;
  status: string;
}

const escalations = ref<Escalation[]>([]);

// ── Fetch ──────────────────────────────────────────────────────────

async function fetchStats(): Promise<void> {
  try {
    const resp = await fetch("/api/cleo/stats");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = (await resp.json()) as CleoStats & { error?: string };
    if (data.error) {
      error.value = data.error;
      return;
    }
    stats.value = {
      total:   data.total   ?? 0,
      done:    data.done    ?? 0,
      active:  data.active  ?? 0,
      pending: data.pending ?? 0,
      blocked: data.blocked ?? 0,
    };
    error.value = null;
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}

async function fetchEscalations(): Promise<void> {
  try {
    const resp = await fetch("/api/cleo/epics");
    if (!resp.ok) return;
    const data = (await resp.json()) as { epics?: EpicSummary[] };
    const epics = data.epics ?? [];

    // Fetch all tasks under each epic and filter for escalations
    const escalationList: Escalation[] = [];
    for (const epic of epics) {
      if (epic.progress.failed > 0 || epic.progress.active > 0) {
        const childResp = await fetch(`/api/cleo/tasks?parent=${encodeURIComponent(epic.id)}`);
        if (!childResp.ok) continue;
        const childData = (await childResp.json()) as { tasks?: TaskSummary[] };
        const tasks = childData.tasks ?? [];
        for (const t of tasks) {
          const s = t.status.toLowerCase();
          const isBlocked = s === "blocked" || s === "failed";
          const isCriticalNotDone = t.priority === "critical" && s !== "done" && s !== "completed";
          if (isBlocked || isCriticalNotDone) {
            escalationList.push({
              id: t.id,
              title: t.title,
              priority: t.priority,
              status: t.status,
            });
          }
        }
      }
    }
    escalations.value = escalationList.slice(0, 5); // cap at 5 items
  } catch {
    // non-fatal — escalations can be missing
  }
}

async function refresh(): Promise<void> {
  loading.value = true;
  await Promise.all([fetchStats(), fetchEscalations()]);
  loading.value = false;
}

// ── Priority badge colours ─────────────────────────────────────────

function priorityClass(priority: string): string {
  switch (priority.toLowerCase()) {
    case "critical": return "badge--red";
    case "high":     return "badge--orange";
    case "medium":   return "badge--yellow";
    default:         return "badge--gray";
  }
}

// ── Lifecycle ──────────────────────────────────────────────────────

let _pollTimer: ReturnType<typeof setInterval> | null = null;

onMounted(() => {
  refresh();
  _pollTimer = setInterval(refresh, 30_000);
});

onUnmounted(() => {
  if (_pollTimer !== null) clearInterval(_pollTimer);
});
</script>

<template>
  <div class="rs">
    <!-- ── Section header ─────────────────────────────────────── -->
    <div class="rs__section-header">
      <span class="rs__section-label">TAC-MASTER METRICS</span>
      <span
        v-if="loading"
        class="rs__spinner"
        title="Refreshing…"
      >⟳</span>
    </div>

    <!-- ── Error state ───────────────────────────────────────── -->
    <div v-if="error" class="rs__error">
      <span class="rs__error-text">{{ error }}</span>
    </div>

    <!-- ── Metrics table ─────────────────────────────────────── -->
    <div class="rs__metrics">
      <div class="rs__metric-row">
        <span class="rs__metric-label">Total Tasks</span>
        <span class="rs__metric-value">{{ stats.total }}</span>
      </div>
      <div class="rs__metric-row">
        <span class="rs__metric-label">In Progress</span>
        <span class="rs__metric-value rs__metric-value--active">{{ stats.active }}</span>
      </div>
      <div class="rs__metric-row">
        <span class="rs__metric-label">Completed</span>
        <span class="rs__metric-value rs__metric-value--done">{{ stats.done }}</span>
      </div>
      <div class="rs__metric-row">
        <span class="rs__metric-label">Failed/Blocked</span>
        <span class="rs__metric-value rs__metric-value--failed">{{ stats.blocked }}</span>
      </div>
      <div class="rs__metric-row">
        <span class="rs__metric-label">Pending</span>
        <span class="rs__metric-value rs__metric-value--pending">{{ stats.pending }}</span>
      </div>
    </div>

    <!-- ── Divider ───────────────────────────────────────────── -->
    <div class="rs__divider" />

    <!-- ── Escalations ───────────────────────────────────────── -->
    <div class="rs__sub-header">ESCALATIONS</div>
    <div class="rs__escalations">
      <div
        v-for="item in escalations"
        :key="item.id"
        class="rs__esc-item"
        :title="`${item.id}: ${item.title} [${item.status}]`"
      >
        <span class="rs__esc-id">{{ item.id }}</span>
        <span class="rs__esc-title">{{ item.title }}</span>
        <span class="rs__badge" :class="priorityClass(item.priority)">
          {{ item.priority.toUpperCase().slice(0, 3) }}
        </span>
      </div>
      <div v-if="escalations.length === 0 && !loading" class="rs__esc-empty">
        <span>NONE</span>
      </div>
    </div>

    <!-- ── Divider ───────────────────────────────────────────── -->
    <div class="rs__divider" />

    <!-- ── Quick links ───────────────────────────────────────── -->
    <div class="rs__sub-header">QUICK LINKS</div>
    <nav class="rs__nav">
      <button
        class="rs__nav-link"
        @click="emit('open-logs')"
      >
        <span class="rs__nav-link-arrow">›</span>
        SYSTEM_LOGS
      </button>
    </nav>
  </div>
</template>

<style scoped>
.rs {
  display: flex;
  flex-direction: column;
  height: 100%;
  font-family: var(--cc-font, ui-monospace, monospace);
  background: var(--cc-surface, #111);
  color: var(--cc-text, #e0e0e0);
  overflow: hidden;
}

/* ── Section header ──────────────────────────────────────────── */
.rs__section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px 6px;
  flex-shrink: 0;
}

.rs__section-label {
  font-size: 8px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--cc-text-muted, #666);
}

.rs__spinner {
  font-size: 10px;
  color: var(--cc-cyan, #00ffcc);
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to   { transform: rotate(360deg); }
}

/* ── Error ───────────────────────────────────────────────────── */
.rs__error {
  padding: 6px 12px;
}
.rs__error-text {
  font-size: 9px;
  color: var(--cc-red, #ff4466);
  word-break: break-all;
}

/* ── Metrics ─────────────────────────────────────────────────── */
.rs__metrics {
  display: flex;
  flex-direction: column;
  padding: 4px 0;
  flex-shrink: 0;
}

.rs__metric-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 5px 12px;
}

.rs__metric-label {
  font-size: 10px;
  color: var(--cc-text-muted, #666);
  letter-spacing: 0.04em;
}

.rs__metric-value {
  font-size: 12px;
  font-weight: 700;
  color: var(--cc-text, #e0e0e0);
  min-width: 24px;
  text-align: right;
}

.rs__metric-value--active  { color: var(--cc-cyan,  #00ffcc); }
.rs__metric-value--done    { color: var(--cc-green, #00ff66); }
.rs__metric-value--failed  { color: var(--cc-red,   #ff4466); }
.rs__metric-value--pending { color: var(--cc-text-muted, #666); }

/* ── Sub-header ──────────────────────────────────────────────── */
.rs__sub-header {
  font-size: 8px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--cc-text-muted, #666);
  padding: 6px 12px 4px;
  flex-shrink: 0;
}

/* ── Escalations ─────────────────────────────────────────────── */
.rs__escalations {
  display: flex;
  flex-direction: column;
  padding: 2px 0 4px;
  flex-shrink: 0;
  max-height: 120px;
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: var(--cc-border-mid, #222) var(--cc-bg, #0a0a0a);
}

.rs__esc-item {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 4px 12px;
  min-height: 24px;
  overflow: hidden;
}

.rs__esc-id {
  font-size: 8px;
  color: var(--cc-cyan, #00ffcc);
  flex-shrink: 0;
  letter-spacing: 0.04em;
}

.rs__esc-title {
  font-size: 9px;
  color: var(--cc-text, #e0e0e0);
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.rs__esc-empty {
  padding: 4px 12px;
  font-size: 9px;
  color: var(--cc-text-dim, #444);
  letter-spacing: 0.08em;
}

/* ── Priority badges ─────────────────────────────────────────── */
.rs__badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 24px;
  height: 13px;
  padding: 0 3px;
  border-radius: 2px;
  font-size: 7px;
  font-weight: 700;
  letter-spacing: 0.04em;
  flex-shrink: 0;
}

.badge--red    { background: rgba(255, 68,102,0.15); color: var(--cc-red,   #ff4466); border: 1px solid rgba(255,68,102,0.3); }
.badge--orange { background: rgba(255,165,  0,0.15); color: #ffa500;                  border: 1px solid rgba(255,165,0,0.3); }
.badge--yellow { background: rgba(255,204,  0,0.15); color: #ffcc00;                  border: 1px solid rgba(255,204,0,0.3); }
.badge--gray   { background: rgba(100,100,100,0.15); color: var(--cc-text-muted,#666);border: 1px solid rgba(100,100,100,0.3); }

/* ── Divider ─────────────────────────────────────────────────── */
.rs__divider {
  height: 1px;
  background: var(--cc-border, #1a1a1a);
  flex-shrink: 0;
  margin: 4px 0;
}

/* ── Bottom nav ──────────────────────────────────────────────── */
.rs__nav {
  display: flex;
  flex-direction: column;
  padding: 4px 0 8px;
  flex-shrink: 0;
}

.rs__nav-link {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 12px;
  background: transparent;
  border: none;
  font-family: var(--cc-font, ui-monospace, monospace);
  font-size: 9px;
  letter-spacing: 0.08em;
  color: var(--cc-text-muted, #666);
  cursor: pointer;
  text-align: left;
  transition: color var(--cc-transition, 0.15s ease),
              background var(--cc-transition, 0.15s ease);
}

.rs__nav-link:hover {
  color: var(--cc-text, #e0e0e0);
  background: var(--cc-surface-raised, #161616);
}

.rs__nav-link-arrow {
  font-size: 11px;
  opacity: 0.6;
}
</style>
