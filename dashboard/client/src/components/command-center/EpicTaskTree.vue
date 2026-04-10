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
    <div v-if="store.error" class="ett-error">{{ store.error }}</div>

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
        <!-- Epic card header -->
        <div class="ett-epic-header" @click="toggleEpic(epic.id)">
          <div class="ett-epic-title-row">
            <span class="ett-epic-chevron">{{ expandedEpics.has(epic.id) ? '▾' : '▸' }}</span>
            <span class="ett-epic-id">{{ epic.id }}</span>
            <span class="ett-epic-title" :title="epic.title">{{ epic.title }}</span>
            <span class="ett-priority-badge" :class="`ett-priority-${epic.priority}`">
              {{ epic.priority.toUpperCase().slice(0, 4) }}
            </span>
            <!-- Epic info icon: click to open modal without toggling expand -->
            <span
              class="ett-info-btn"
              title="View epic details"
              @click.stop="store.openTaskModal(epic.id)"
            >&#9432;</span>
            <!-- Add child task button -->
            <span
              class="ett-add-btn"
              title="Add child task"
              @click.stop="openCreateForm(epic.id)"
            >+</span>
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
            <span class="ett-progress-counts">{{ epic.progress.done }}/{{ epic.progress.total }}</span>
          </div>
        </div>

        <!-- Task list (visible when expanded) -->
        <div v-if="expandedEpics.has(epic.id)" class="ett-task-list">
          <div v-if="!store.tasksByEpic[epic.id]" class="ett-task-loading">
            <span class="ett-spinner">&#9679;</span> loading tasks...
          </div>
          <div v-else-if="store.tasksByEpic[epic.id].length === 0" class="ett-no-tasks">
            No child tasks found.
          </div>
          <div
            v-for="task in store.tasksByEpic[epic.id]"
            :key="task.id"
            class="ett-task-row"
            :class="`ett-task-${normaliseStatus(task.status)}`"
            :title="`${task.id}: ${task.title} — click for details`"
            @click="store.openTaskModal(task.id)"
          >
            <span class="ett-task-icon" :class="`ett-icon-${normaliseStatus(task.status)}`">
              {{ statusIcon(task.status) }}
            </span>
            <span class="ett-task-id">{{ task.id }}</span>
            <span class="ett-task-title">{{ task.title }}</span>
            <!-- Subtask progress bar if task has children -->
            <span
              v-if="task.children_count && task.children_count > 0"
              class="ett-subtask-progress"
              :title="`${task.children_done}/${task.children_count} subtasks done`"
            >
              <span class="ett-subtask-bar">
                <span
                  class="ett-subtask-fill"
                  :class="subFillClass(task.children_done ?? 0, task.children_count)"
                  :style="{ width: `${Math.round(((task.children_done ?? 0) / task.children_count) * 100)}%` }"
                ></span>
              </span>
              <span class="ett-subtask-label">{{ task.children_done }}/{{ task.children_count }}</span>
            </span>
            <span v-if="task.size" class="ett-size-badge">{{ task.size }}</span>
            <span class="ett-priority-dot" :class="`ett-pdot-${task.priority}`" :title="task.priority"></span>
          </div>

          <!-- Inline create form -->
          <div v-if="createFormEpicId === epic.id" class="ett-create-form" @click.stop>
            <div class="ett-create-form-title">New task under {{ epic.id }}</div>
            <input
              class="ett-create-input"
              v-model="createTitle"
              placeholder="Task title (required)"
              :disabled="createBusy"
              @keydown.enter="submitCreate"
              @keydown.escape="closeCreateForm"
            />
            <div class="ett-create-row">
              <select class="ett-create-select" v-model="createPriority" :disabled="createBusy">
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
                <option value="critical">Critical</option>
              </select>
              <select class="ett-create-select" v-model="createSize" :disabled="createBusy">
                <option value="small">Small</option>
                <option value="medium">Medium</option>
                <option value="large">Large</option>
              </select>
              <button class="ett-create-btn ett-create-submit" @click="submitCreate" :disabled="createBusy || !createTitle.trim()">
                {{ createBusy ? '...' : 'Add' }}
              </button>
              <button class="ett-create-btn ett-create-cancel" @click="closeCreateForm" :disabled="createBusy">
                Cancel
              </button>
            </div>
            <div v-if="createError" class="ett-create-error">{{ createError }}</div>
          </div>
        </div>
      </div>
    </div>

    <!-- Task detail modal (Teleport to body is inside the component) -->
    <TaskDetailModal />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from "vue";
import { useCleoStore, type EpicSummary } from "../../stores/cleoStore";
import TaskDetailModal from "./TaskDetailModal.vue";

const store = useCleoStore();
const expandedEpics = ref<Set<string>>(new Set());

// Create form state
const createFormEpicId = ref<string | null>(null);
const createTitle = ref("");
const createPriority = ref("medium");
const createSize = ref("medium");
const createBusy = ref(false);
const createError = ref("");

function openCreateForm(epicId: string): void {
  createFormEpicId.value = epicId;
  createTitle.value = "";
  createPriority.value = "medium";
  createSize.value = "medium";
  createError.value = "";
  // Expand the epic so form is visible
  if (!expandedEpics.value.has(epicId)) {
    expandedEpics.value.add(epicId);
    store.selectEpic(epicId);
    expandedEpics.value = new Set(expandedEpics.value);
  }
}

function closeCreateForm(): void {
  createFormEpicId.value = null;
  createError.value = "";
}

async function submitCreate(): Promise<void> {
  if (!createFormEpicId.value || !createTitle.value.trim()) return;
  createBusy.value = true;
  createError.value = "";
  try {
    const resp = await fetch("/api/cleo/task", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: createTitle.value.trim(),
        parent_id: createFormEpicId.value,
        priority: createPriority.value,
        size: createSize.value,
        type: "task",
      }),
    });
    const data = (await resp.json()) as { ok: boolean; task?: { id: string }; error?: string };
    if (data.ok) {
      const parentId = createFormEpicId.value;
      closeCreateForm();
      // Refresh the epic's task list
      await store.fetchTasks(parentId);
      await store.fetchEpics();
    } else {
      createError.value = data.error ?? "Failed to create task.";
    }
  } catch (e: unknown) {
    createError.value = e instanceof Error ? e.message : "Network error.";
  } finally {
    createBusy.value = false;
  }
}

function toggleEpic(id: string): void {
  if (expandedEpics.value.has(id)) {
    expandedEpics.value.delete(id);
    if (createFormEpicId.value === id) closeCreateForm();
  } else {
    expandedEpics.value.add(id);
    store.selectEpic(id);
  }
  expandedEpics.value = new Set(expandedEpics.value);
}

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

function subFillClass(done: number, total: number): string {
  if (!total) return "ett-subfill-low";
  const pct = (done / total) * 100;
  if (pct >= 100) return "ett-subfill-done";
  if (pct >= 60) return "ett-subfill-high";
  if (pct >= 30) return "ett-subfill-mid";
  return "ett-subfill-low";
}

function relativeTime(d: Date): string {
  const secs = Math.floor((Date.now() - d.getTime()) / 1000);
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  return `${Math.floor(mins / 60)}h ago`;
}

onMounted(() => { store.initialize(); });
onUnmounted(() => { store.stopPolling(); });
</script>

<style scoped>
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

.ett-epic-list {
  flex: 1;
  overflow-y: auto;
  padding: 6px 0;
}

.ett-epic-list::-webkit-scrollbar { width: 4px; }
.ett-epic-list::-webkit-scrollbar-track { background: transparent; }
.ett-epic-list::-webkit-scrollbar-thumb { background: #21262d; border-radius: 2px; }

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

.ett-spinner { animation: pulse 1s ease-in-out infinite; }

.ett-task-row {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 4px 14px;
  border-bottom: 1px solid #161b22;
  transition: background 0.1s;
  cursor: pointer;
}
.ett-task-row:hover { background: #1a1e24; }
.ett-task-row:last-child { border-bottom: none; }

.ett-task-done   { opacity: 0.55; }

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

.ett-priority-dot {
  flex-shrink: 0;
  width: 6px;
  height: 6px;
  border-radius: 50%;
}
.ett-pdot-critical { background: #f78166; }
.ett-pdot-high     { background: #ffa657; }
.ett-pdot-medium   { background: #d29922; }
.ett-pdot-low      { background: #484f58; }

/* Info button on epic header */
.ett-info-btn {
  flex-shrink: 0;
  font-size: 12px;
  color: #484f58;
  cursor: pointer;
  line-height: 1;
  padding: 0 2px;
  border-radius: 2px;
  transition: color 0.15s;
}
.ett-info-btn:hover { color: #58a6ff; }

/* Add child task button (+) on epic row */
.ett-add-btn {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: transparent;
  border: 1px solid #30363d;
  color: #484f58;
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s, color 0.15s, border-color 0.15s, background 0.15s;
}
.ett-epic-header:hover .ett-add-btn { opacity: 1; }
.ett-add-btn:hover { color: #39d0d8; border-color: #39d0d8; background: #051a1a; }

/* Inline create form */
.ett-create-form {
  padding: 8px 12px 10px;
  border-top: 1px solid #161b22;
  background: #0d1117;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.ett-create-form-title {
  font-size: 10px;
  color: #484f58;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  font-weight: 600;
}
.ett-create-input {
  width: 100%;
  box-sizing: border-box;
  background: #161b22;
  color: #c9d1d9;
  border: 1px solid #30363d;
  border-radius: 4px;
  padding: 5px 8px;
  font-family: ui-monospace, "Cascadia Code", Menlo, Consolas, monospace;
  font-size: 11px;
  transition: border-color 0.15s;
}
.ett-create-input:focus { outline: none; border-color: #39d0d8; }
.ett-create-input:disabled { opacity: 0.5; }
.ett-create-row {
  display: flex;
  gap: 5px;
  align-items: center;
}
.ett-create-select {
  background: #161b22;
  color: #8b949e;
  border: 1px solid #30363d;
  border-radius: 4px;
  padding: 3px 6px;
  font-family: ui-monospace, "Cascadia Code", Menlo, Consolas, monospace;
  font-size: 10px;
  cursor: pointer;
}
.ett-create-select:disabled { opacity: 0.5; }
.ett-create-btn {
  font-family: ui-monospace, "Cascadia Code", Menlo, Consolas, monospace;
  font-size: 10px;
  background: #1a1a1a;
  color: #8b949e;
  border: 1px solid #30363d;
  border-radius: 20px;
  padding: 3px 10px;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
}
.ett-create-btn:disabled { opacity: 0.45; cursor: not-allowed; }
.ett-create-submit:hover:not(:disabled) { border-color: #3fb950; color: #3fb950; }
.ett-create-cancel:hover:not(:disabled) { border-color: #484f58; color: #484f58; }
.ett-create-error {
  font-size: 10px;
  color: #f78166;
  padding: 2px 0;
}

/* Subtask progress bar inline in task rows */
.ett-subtask-progress {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}
.ett-subtask-bar {
  display: inline-block;
  width: 32px;
  height: 3px;
  background: #21262d;
  border-radius: 2px;
  overflow: hidden;
}
.ett-subtask-fill {
  display: block;
  height: 100%;
  border-radius: 2px;
  transition: width 0.3s ease;
}
.ett-subfill-done { background: #3fb950; }
.ett-subfill-high { background: #3fb950; }
.ett-subfill-mid  { background: #d29922; }
.ett-subfill-low  { background: #484f58; }
.ett-subtask-label {
  font-size: 9px;
  color: #484f58;
  white-space: nowrap;
}

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
