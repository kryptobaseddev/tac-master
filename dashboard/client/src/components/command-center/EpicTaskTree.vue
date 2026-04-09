<template>
  <div class="epic-task-tree">
    <!-- Header -->
    <div class="ett-header">
      <span class="ett-title">EPIC &amp; TASK TREE</span>
      <div class="ett-header-right">
        <span v-if="store.loading" class="ett-loading-dot" title="Loading..."></span>
        <span v-if="store.lastFetched" class="ett-last-fetched" :title="store.lastFetched.toISOString()">
          {{ relativeTime(store.lastFetched) }}
        </span>
        <button class="ett-refresh-btn" @click="store.fetchEpics()" title="Refresh">&#8635;</button>
      </div>
    </div>

    <!-- Error banner -->
    <div v-if="store.error" class="ett-error">
      {{ store.error }}
    </div>

    <!-- Empty state -->
    <div v-if="!store.loading && store.epics.length === 0 && !store.error" class="ett-empty">
      No epics found.
    </div>

    <!-- Epic cards -->
    <div class="ett-epic-list">
      <div
        v-for="epic in store.epics"
        :key="epic.id"
        class="ett-epic-card"
        :class="{
          'ett-epic-active': epic.id === store.activeEpicId,
          'ett-epic-done': epic.status === 'done',
          'ett-epic-expanded': expandedEpics.has(epic.id),
        }"
      >
        <!-- Epic card header (clickable to expand/collapse) -->
        <div class="ett-epic-header" @click="toggleEpic(epic.id)">
          <div class="ett-epic-title-row">
            <span class="ett-epic-chevron">{{ expandedEpics.has(epic.id) ? '▾' : '▸' }}</span>
            <span class="ett-epic-id">{{ epic.id }}</span>
            <span class="ett-epic-title" :title="epic.title">{{ epic.title }}</span>
            <span class="ett-priority-badge" :class="`ett-priority-${epic.priority}`">
              {{ epic.priority.toUpperCase().slice(0, 4) }}
            </span>
          </div>

          <!-- Progress bar -->
          <div class="ett-progress-row">
            <div class="ett-progress-bar-track">
              <div
                class="ett-progress-bar-fill"
                :class="progressFillClass(epic)"
                :style="{ width: `${epic.pct}%` }"
              ></div>
            </div>
            <span class="ett-progress-label">{{ epic.pct }}%</span>
            <span class="ett-progress-counts">
              {{ epic.progress.done }}/{{ epic.progress.total }}
            </span>
          </div>
        </div>

        <!-- Task list (visible when expanded) -->
        <div v-if="expandedEpics.has(epic.id)" class="ett-task-list">
          <!-- Loading tasks -->
          <div v-if="!store.tasksByEpic[epic.id]" class="ett-task-loading">
            <span class="ett-spinner">&#9679;</span> loading tasks…
          </div>
          <!-- No tasks -->
          <div
            v-else-if="store.tasksByEpic[epic.id].length === 0"
            class="ett-no-tasks"
          >
            No child tasks found.
          </div>
          <!-- Task rows -->
          <div
            v-for="task in store.tasksByEpic[epic.id]"
            :key="task.id"
            class="ett-task-row"
            :class="`ett-task-${normaliseStatus(task.status)}`"
            :title="`${task.id}: ${task.title}`"
          >
            <span class="ett-task-icon" :class="`ett-icon-${normaliseStatus(task.status)}`">
              {{ statusIcon(task.status) }}
            </span>
            <span class="ett-task-id">{{ task.id }}</span>
            <span class="ett-task-title">{{ task.title }}</span>
            <span v-if="task.size" class="ett-size-badge">{{ task.size }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from "vue";
import { useCleoStore, type EpicSummary } from "../../stores/cleoStore";

const store = useCleoStore();

// Track which epics are expanded locally
const expandedEpics = ref<Set<string>>(new Set());

function toggleEpic(id: string): void {
  if (expandedEpics.value.has(id)) {
    expandedEpics.value.delete(id);
  } else {
    expandedEpics.value.add(id);
    store.selectEpic(id);
  }
  // Trigger reactivity
  expandedEpics.value = new Set(expandedEpics.value);
}

// ---------------------------------------------------------------------------
// Status helpers
// ---------------------------------------------------------------------------

function normaliseStatus(status: string): string {
  const s = (status ?? "").toLowerCase();
  if (s === "done" || s === "completed" || s === "succeeded") return "done";
  if (s === "active" || s === "in_progress" || s === "in-progress" || s === "running") return "active";
  if (s === "failed" || s === "blocked" || s === "cancelled" || s === "canceled") return "failed";
  return "pending";
}

function statusIcon(status: string): string {
  switch (normaliseStatus(status)) {
    case "done":    return "✓";
    case "active":  return "→";
    case "failed":  return "✗";
    default:        return "○";
  }
}

function progressFillClass(epic: EpicSummary): string {
  if (epic.status === "done") return "ett-fill-done";
  if (epic.progress.active > 0) return "ett-fill-active";
  if (epic.pct >= 75) return "ett-fill-high";
  if (epic.pct >= 40) return "ett-fill-mid";
  return "ett-fill-low";
}

// ---------------------------------------------------------------------------
// Time helper
// ---------------------------------------------------------------------------

function relativeTime(d: Date): string {
  const secs = Math.floor((Date.now() - d.getTime()) / 1000);
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  return `${Math.floor(mins / 60)}h ago`;
}

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

onMounted(() => {
  store.initialize();
});

onUnmounted(() => {
  store.stopPolling();
});
</script>

<style scoped>
/* ------------------------------------------------------------------ */
/* Container                                                            */
/* ------------------------------------------------------------------ */
.epic-task-tree {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: #0d1117;
  color: #c9d1d9;
  font-family: ui-monospace, "Cascadia Code", Menlo, Consolas, monospace;
  font-size: 12px;
  overflow: hidden;
}

/* ------------------------------------------------------------------ */
/* Header                                                               */
/* ------------------------------------------------------------------ */
.ett-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px 6px;
  border-bottom: 1px solid #21262d;
  flex-shrink: 0;
}

.ett-title {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.12em;
  color: #8b949e;
  text-transform: uppercase;
}

.ett-header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.ett-loading-dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #58a6ff;
  animation: pulse 1s ease-in-out infinite;
}

.ett-last-fetched {
  font-size: 10px;
  color: #484f58;
}

.ett-refresh-btn {
  background: none;
  border: none;
  color: #484f58;
  cursor: pointer;
  font-size: 14px;
  line-height: 1;
  padding: 0 2px;
  transition: color 0.15s;
}
.ett-refresh-btn:hover { color: #8b949e; }

/* ------------------------------------------------------------------ */
/* Error / empty states                                                 */
/* ------------------------------------------------------------------ */
.ett-error {
  margin: 8px 12px;
  padding: 6px 10px;
  background: #2d1b1b;
  border: 1px solid #6e2020;
  border-radius: 4px;
  color: #f78166;
  font-size: 11px;
}

.ett-empty {
  padding: 16px 12px;
  color: #484f58;
  text-align: center;
}

/* ------------------------------------------------------------------ */
/* Epic list scroll area                                                */
/* ------------------------------------------------------------------ */
.ett-epic-list {
  flex: 1;
  overflow-y: auto;
  padding: 6px 0;
}

.ett-epic-list::-webkit-scrollbar { width: 4px; }
.ett-epic-list::-webkit-scrollbar-track { background: transparent; }
.ett-epic-list::-webkit-scrollbar-thumb { background: #21262d; border-radius: 2px; }

/* ------------------------------------------------------------------ */
/* Epic card                                                            */
/* ------------------------------------------------------------------ */
.ett-epic-card {
  border-left: 2px solid #21262d;
  margin: 2px 8px;
  border-radius: 4px;
  overflow: hidden;
  transition: border-color 0.2s;
}

.ett-epic-card:hover { border-left-color: #388bfd; }
.ett-epic-active  { border-left-color: #3fb950 !important; }
.ett-epic-done    { opacity: 0.65; }
.ett-epic-expanded { border-left-color: #58a6ff; }

.ett-epic-header {
  padding: 8px 10px 6px;
  cursor: pointer;
  background: #161b22;
  transition: background 0.15s;
}
.ett-epic-header:hover { background: #1c2128; }

/* ------------------------------------------------------------------ */
/* Epic title row                                                       */
/* ------------------------------------------------------------------ */
.ett-epic-title-row {
  display: flex;
  align-items: baseline;
  gap: 5px;
  margin-bottom: 5px;
}

.ett-epic-chevron {
  color: #484f58;
  font-size: 10px;
  flex-shrink: 0;
  width: 10px;
}

.ett-epic-id {
  color: #8b949e;
  font-size: 10px;
  flex-shrink: 0;
}

.ett-epic-title {
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: #e6edf3;
  font-size: 11px;
  font-weight: 600;
}

/* Priority badges */
.ett-priority-badge {
  flex-shrink: 0;
  font-size: 9px;
  padding: 1px 4px;
  border-radius: 3px;
  font-weight: 700;
  letter-spacing: 0.05em;
}
.ett-priority-critical { background: #6e0909; color: #ffa198; }
.ett-priority-high     { background: #5a2a05; color: #ffa657; }
.ett-priority-medium   { background: #212830; color: #8b949e; }
.ett-priority-low      { background: #161b22; color: #484f58; }

/* ------------------------------------------------------------------ */
/* Progress bar                                                         */
/* ------------------------------------------------------------------ */
.ett-progress-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.ett-progress-bar-track {
  flex: 1;
  height: 4px;
  background: #21262d;
  border-radius: 2px;
  overflow: hidden;
}

.ett-progress-bar-fill {
  height: 100%;
  border-radius: 2px;
  transition: width 0.4s ease;
}

.ett-fill-done   { background: #3fb950; }
.ett-fill-active { background: #58a6ff; animation: shimmer 2s linear infinite; }
.ett-fill-high   { background: #3fb950; }
.ett-fill-mid    { background: #d29922; }
.ett-fill-low    { background: #484f58; }

.ett-progress-label {
  font-size: 10px;
  color: #8b949e;
  min-width: 28px;
  text-align: right;
}

.ett-progress-counts {
  font-size: 10px;
  color: #484f58;
}

/* ------------------------------------------------------------------ */
/* Task list                                                            */
/* ------------------------------------------------------------------ */
.ett-task-list {
  background: #0d1117;
  border-top: 1px solid #21262d;
}

.ett-task-loading,
.ett-no-tasks {
  padding: 8px 16px;
  color: #484f58;
  font-size: 11px;
}

.ett-spinner {
  animation: pulse 1s ease-in-out infinite;
}

.ett-task-row {
  display: flex;
  align-items: baseline;
  gap: 5px;
  padding: 4px 14px;
  border-bottom: 1px solid #161b22;
  transition: background 0.1s;
}
.ett-task-row:hover { background: #161b22; }
.ett-task-row:last-child { border-bottom: none; }

/* Task status classes */
.ett-task-done   { opacity: 0.55; }
.ett-task-active { }
.ett-task-failed { }
.ett-task-pending { }

/* Status icon */
.ett-task-icon {
  flex-shrink: 0;
  width: 14px;
  text-align: center;
  font-size: 11px;
}
.ett-icon-done    { color: #3fb950; }
.ett-icon-active  { color: #58a6ff; animation: pulse 1.2s ease-in-out infinite; }
.ett-icon-failed  { color: #f78166; }
.ett-icon-pending { color: #484f58; }

.ett-task-id {
  flex-shrink: 0;
  color: #484f58;
  font-size: 10px;
}

.ett-task-title {
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: #c9d1d9;
  font-size: 11px;
}

.ett-size-badge {
  flex-shrink: 0;
  font-size: 9px;
  padding: 1px 4px;
  border-radius: 3px;
  background: #21262d;
  color: #8b949e;
  text-transform: uppercase;
}

/* ------------------------------------------------------------------ */
/* Animations                                                           */
/* ------------------------------------------------------------------ */
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0.4; }
}

@keyframes shimmer {
  0%   { opacity: 1; }
  50%  { opacity: 0.7; }
  100% { opacity: 1; }
}
</style>
