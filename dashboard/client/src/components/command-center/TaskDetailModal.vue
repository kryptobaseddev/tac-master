<template>
  <!-- Backdrop -->
  <Teleport to="body">
    <Transition name="tdm-fade">
      <div
        v-if="store.activeModal || store.modalLoading"
        class="tdm-backdrop"
        @click.self="store.closeModal()"
        @keydown.escape="store.closeModal()"
        tabindex="-1"
        ref="backdropRef"
      >
        <!-- Loading spinner -->
        <div v-if="store.modalLoading && !store.activeModal" class="tdm-loading">
          <span class="tdm-spinner"></span>
          <span class="tdm-loading-text">Loading task...</span>
        </div>

        <!-- Modal card -->
        <Transition name="tdm-scale">
          <div
            v-if="store.activeModal"
            class="tdm-card"
            role="dialog"
            aria-modal="true"
            :aria-label="`Task ${store.activeModal.id}`"
          >
            <!-- Header -->
            <div class="tdm-header">
              <div class="tdm-header-left">
                <span class="tdm-task-id">{{ store.activeModal.id }}</span>
                <span class="tdm-sep">·</span>
                <span class="tdm-task-title">{{ store.activeModal.title }}</span>
              </div>
              <button class="tdm-close-btn" @click="store.closeModal()" title="Close (Esc)">✕</button>
            </div>

            <div class="tdm-divider"></div>

            <!-- Meta row -->
            <div class="tdm-meta-row">
              <div class="tdm-meta-item">
                <span class="tdm-meta-label">Status</span>
                <span class="tdm-status-badge" :class="`tdm-status-${normaliseStatus(store.activeModal.status)}`">
                  <span class="tdm-status-dot"></span>
                  {{ store.activeModal.status.toUpperCase() }}
                </span>
              </div>
              <div class="tdm-meta-item">
                <span class="tdm-meta-label">Priority</span>
                <span class="tdm-priority-badge" :class="`tdm-priority-${store.activeModal.priority}`">
                  {{ store.activeModal.priority.toUpperCase() }}
                </span>
              </div>
              <div v-if="store.activeModal.size" class="tdm-meta-item">
                <span class="tdm-meta-label">Size</span>
                <span class="tdm-value-badge">{{ store.activeModal.size.toUpperCase() }}</span>
              </div>
              <div v-if="store.activeModal.type" class="tdm-meta-item">
                <span class="tdm-meta-label">Type</span>
                <span class="tdm-value-badge">{{ store.activeModal.type.toUpperCase() }}</span>
              </div>
              <div v-if="store.activeModal.parent_id" class="tdm-meta-item">
                <span class="tdm-meta-label">Parent</span>
                <span class="tdm-link-badge" @click="navigateToTask(store.activeModal.parent_id!)">
                  {{ store.activeModal.parent_id }}
                </span>
              </div>
              <div v-if="store.activeModal.assignee" class="tdm-meta-item">
                <span class="tdm-meta-label">Assignee</span>
                <span class="tdm-value-badge tdm-agent-badge">{{ store.activeModal.assignee }}</span>
              </div>
              <div v-if="store.activeModal.depends && store.activeModal.depends.length > 0" class="tdm-meta-item">
                <span class="tdm-meta-label">Depends</span>
                <span class="tdm-value-badge">{{ store.activeModal.depends.join(", ") }}</span>
              </div>
            </div>

            <!-- Scrollable body -->
            <div class="tdm-body">
              <!-- Description -->
              <section v-if="store.activeModal.description" class="tdm-section">
                <div class="tdm-section-label">DESCRIPTION</div>
                <p class="tdm-description">{{ store.activeModal.description }}</p>
              </section>

              <!-- Acceptance Criteria -->
              <section v-if="store.activeModal.acceptance && store.activeModal.acceptance.length > 0" class="tdm-section">
                <div class="tdm-section-label">ACCEPTANCE CRITERIA</div>
                <ul class="tdm-ac-list">
                  <li
                    v-for="(ac, i) in store.activeModal.acceptance"
                    :key="i"
                    class="tdm-ac-item"
                    :class="`tdm-ac-${acStatus(store.activeModal.status)}`"
                  >
                    <span class="tdm-ac-icon">{{ acIcon(store.activeModal.status) }}</span>
                    <span class="tdm-ac-text">{{ ac }}</span>
                  </li>
                </ul>
              </section>

              <!-- Children progress (if this task has sub-tasks) -->
              <section v-if="store.activeModal.children && store.activeModal.children.length > 0" class="tdm-section">
                <div class="tdm-section-label">
                  SUBTASKS
                  <span class="tdm-section-count">
                    {{ doneChildren }}/{{ store.activeModal.children.length }}
                    <span class="tdm-section-pct">({{ childPct }}%)</span>
                  </span>
                </div>
                <div class="tdm-child-progress-bar">
                  <div class="tdm-child-fill" :style="{ width: `${childPct}%` }" :class="`tdm-fill-${childFillClass}`"></div>
                </div>
                <ul class="tdm-children-list">
                  <li
                    v-for="child in store.activeModal.children"
                    :key="child.id"
                    class="tdm-child-item"
                    :class="`tdm-child-${normaliseStatus(child.status)}`"
                    @click="navigateToTask(child.id)"
                    title="Click to view this task"
                  >
                    <span class="tdm-child-icon">{{ statusIcon(child.status) }}</span>
                    <span class="tdm-child-id">{{ child.id }}</span>
                    <span class="tdm-child-title">{{ child.title }}</span>
                  </li>
                </ul>
              </section>

              <!-- Notes -->
              <section v-if="store.activeModal.notes && store.activeModal.notes.length > 0" class="tdm-section">
                <div class="tdm-section-label">NOTES ({{ store.activeModal.notes.length }} most recent)</div>
                <ul class="tdm-notes-list">
                  <li v-for="(note, i) in store.activeModal.notes" :key="i" class="tdm-note-item">
                    <span class="tdm-note-text">{{ note }}</span>
                  </li>
                </ul>
              </section>

              <!-- GitHub Issue Link -->
              <section v-if="store.activeModal.github_url" class="tdm-section">
                <div class="tdm-section-label">GITHUB ISSUE</div>
                <div class="tdm-github-row">
                  <span class="tdm-github-icon">&#128279;</span>
                  <span class="tdm-github-text">{{ githubLabel(store.activeModal.github_url) }}</span>
                  <a
                    class="tdm-github-link"
                    :href="store.activeModal.github_url"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    View on GitHub →
                  </a>
                </div>
              </section>

              <!-- Labels -->
              <section v-if="store.activeModal.labels && store.activeModal.labels.length > 0" class="tdm-section">
                <div class="tdm-section-label">LABELS</div>
                <div class="tdm-labels-row">
                  <span v-for="label in store.activeModal.labels" :key="label" class="tdm-label-chip">
                    {{ label }}
                  </span>
                </div>
              </section>
            </div>

            <!-- Action bar -->
            <div class="tdm-action-bar">
              <!-- Add Note -->
              <button class="tdm-action-btn tdm-action-note" @click="showNoteInput = !showNoteInput" :disabled="actionBusy">
                + Add Note
              </button>

              <!-- Change Status -->
              <select class="tdm-action-btn tdm-action-status" v-model="newStatus" @change="updateStatus" :disabled="actionBusy">
                <option value="">Change Status...</option>
                <option value="pending">Pending</option>
                <option value="active">Active</option>
                <option value="done">Done</option>
                <option value="blocked">Blocked</option>
              </select>

              <!-- Queue for Dispatch -->
              <button
                v-if="store.activeModal.status === 'pending' || store.activeModal.status === 'blocked'"
                class="tdm-action-btn tdm-action-queue"
                @click="queueTask"
                :disabled="actionBusy"
              >
                &#9654; Queue
              </button>
            </div>

            <!-- Note input (shown when Add Note clicked) -->
            <div v-if="showNoteInput" class="tdm-note-compose">
              <textarea
                class="tdm-note-textarea"
                v-model="noteText"
                placeholder="Type your note..."
                rows="3"
                :disabled="actionBusy"
              ></textarea>
              <div class="tdm-note-compose-actions">
                <button class="tdm-action-btn tdm-action-submit" @click="submitNote" :disabled="actionBusy || !noteText.trim()">
                  Submit
                </button>
                <button class="tdm-action-btn tdm-action-cancel" @click="showNoteInput = false; noteText = ''" :disabled="actionBusy">
                  Cancel
                </button>
              </div>
            </div>

            <!-- Toast notification -->
            <Transition name="tdm-toast-slide">
              <div v-if="toastMsg" class="tdm-toast" :class="toastErr ? 'tdm-toast-err' : 'tdm-toast-ok'">
                {{ toastMsg }}
              </div>
            </Transition>
          </div>
        </Transition>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, ref, watch, nextTick, onMounted, onUnmounted } from "vue";
import { useCleoStore } from "../../stores/cleoStore";

const store = useCleoStore();
const backdropRef = ref<HTMLElement | null>(null);

// Action bar state
const showNoteInput = ref(false);
const noteText = ref("");
const newStatus = ref("");
const actionBusy = ref(false);
const toastMsg = ref("");
const toastErr = ref(false);
let _toastTimer: ReturnType<typeof setTimeout> | null = null;

function showToast(msg: string, err = false): void {
  toastMsg.value = msg;
  toastErr.value = err;
  if (_toastTimer) clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => { toastMsg.value = ""; }, 3000);
}

watch(() => store.activeModal, () => {
  showNoteInput.value = false;
  noteText.value = "";
  newStatus.value = "";
  actionBusy.value = false;
});

async function submitNote(): Promise<void> {
  if (!store.activeModal || !noteText.value.trim()) return;
  actionBusy.value = true;
  try {
    const resp = await fetch(`/api/cleo/task/${encodeURIComponent(store.activeModal.id)}/note`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: noteText.value.trim() }),
    });
    const data = (await resp.json()) as { ok: boolean; error?: string };
    if (data.ok) {
      showToast("Note added.");
      noteText.value = "";
      showNoteInput.value = false;
      await store.openTaskModal(store.activeModal.id);
    } else {
      showToast(data.error ?? "Failed to add note.", true);
    }
  } catch (e: unknown) {
    showToast(e instanceof Error ? e.message : "Network error.", true);
  } finally {
    actionBusy.value = false;
  }
}

async function updateStatus(): Promise<void> {
  if (!store.activeModal || !newStatus.value) return;
  actionBusy.value = true;
  const status = newStatus.value;
  newStatus.value = "";
  try {
    const resp = await fetch(`/api/cleo/task/${encodeURIComponent(store.activeModal.id)}/status`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    });
    const data = (await resp.json()) as { ok: boolean; error?: string };
    if (data.ok) {
      showToast(`Status updated to ${status}.`);
      await store.openTaskModal(store.activeModal.id);
      await store.fetchEpics();
    } else {
      showToast(data.error ?? "Failed to update status.", true);
    }
  } catch (e: unknown) {
    showToast(e instanceof Error ? e.message : "Network error.", true);
  } finally {
    actionBusy.value = false;
  }
}

async function queueTask(): Promise<void> {
  if (!store.activeModal) return;
  actionBusy.value = true;
  try {
    const resp = await fetch(`/api/cleo/task/${encodeURIComponent(store.activeModal.id)}/queue`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    const data = (await resp.json()) as { ok: boolean; queued: boolean; dispatched: boolean; error?: string };
    if (data.ok) {
      showToast(data.dispatched ? "Queued and dispatched." : "Queued for dispatch.");
      await store.openTaskModal(store.activeModal.id);
      await store.fetchEpics();
    } else {
      showToast(data.error ?? "Failed to queue task.", true);
    }
  } catch (e: unknown) {
    showToast(e instanceof Error ? e.message : "Network error.", true);
  } finally {
    actionBusy.value = false;
  }
}

// Focus backdrop so Escape key works
watch(
  () => store.activeModal,
  async (val) => {
    if (val) {
      await nextTick();
      backdropRef.value?.focus();
    }
  },
);

function handleKeydown(e: KeyboardEvent): void {
  if (e.key === "Escape" && (store.activeModal || store.modalLoading)) {
    store.closeModal();
  }
}

onMounted(() => { document.addEventListener("keydown", handleKeydown); });
onUnmounted(() => { document.removeEventListener("keydown", handleKeydown); });

function normaliseStatus(status: string): string {
  const s = (status ?? "").toLowerCase();
  if (s === "done" || s === "completed" || s === "succeeded") return "done";
  if (s === "active" || s === "in_progress" || s === "in-progress" || s === "running") return "active";
  if (s === "failed" || s === "blocked" || s === "cancelled" || s === "canceled") return "failed";
  return "pending";
}

function statusIcon(status: string): string {
  switch (normaliseStatus(status)) {
    case "done":   return "✓";
    case "active": return "→";
    case "failed": return "✗";
    default:       return "○";
  }
}

function acStatus(taskStatus: string): string {
  return normaliseStatus(taskStatus);
}

function acIcon(taskStatus: string): string {
  const s = normaliseStatus(taskStatus);
  if (s === "done") return "✓";
  if (s === "failed") return "✗";
  return "○";
}

const doneChildren = computed(() => {
  if (!store.activeModal?.children) return 0;
  return store.activeModal.children.filter(
    (c) => normaliseStatus(c.status) === "done"
  ).length;
});

const childPct = computed(() => {
  const total = store.activeModal?.children?.length ?? 0;
  if (total === 0) return 0;
  return Math.round((doneChildren.value / total) * 100);
});

const childFillClass = computed(() => {
  const pct = childPct.value;
  if (pct === 100) return "complete";
  if (pct >= 60) return "high";
  if (pct >= 30) return "mid";
  return "low";
});

function githubLabel(url: string): string {
  // Extract "owner/repo#N" from URL
  const m = url.match(/github\.com\/([\w-]+\/[\w-]+)\/issues\/(\d+)/);
  return m ? `${m[1]}#${m[2]}` : url;
}

function navigateToTask(taskId: string | null | undefined): void {
  if (!taskId) return;
  store.openTaskModal(taskId);
}
</script>

<style scoped>
/* Backdrop */
.tdm-backdrop {
  position: fixed;
  inset: 0;
  z-index: 1000;
  background: rgba(0, 0, 0, 0.82);
  backdrop-filter: blur(3px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  outline: none;
}

/* Loading */
.tdm-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  color: #8b949e;
  font-family: ui-monospace, "Cascadia Code", Menlo, Consolas, monospace;
  font-size: 12px;
}
.tdm-spinner {
  display: inline-block;
  width: 20px;
  height: 20px;
  border: 2px solid #21262d;
  border-top-color: #58a6ff;
  border-radius: 50%;
  animation: tdm-spin 0.8s linear infinite;
}
.tdm-loading-text { color: #484f58; }

/* Card */
.tdm-card {
  background: #111317;
  border: 1px solid #1e2229;
  border-radius: 8px;
  width: 100%;
  max-width: 680px;
  max-height: 88vh;
  display: flex;
  flex-direction: column;
  font-family: ui-monospace, "Cascadia Code", Menlo, Consolas, monospace;
  font-size: 12px;
  color: #c9d1d9;
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.6);
  overflow: hidden;
}

/* Header */
.tdm-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 16px 12px;
  flex-shrink: 0;
}
.tdm-header-left {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex: 1;
  min-width: 0;
}
.tdm-task-id {
  font-size: 11px;
  font-weight: 700;
  color: #58a6ff;
  flex-shrink: 0;
  letter-spacing: 0.04em;
}
.tdm-sep {
  color: #484f58;
  flex-shrink: 0;
}
.tdm-task-title {
  font-size: 13px;
  font-weight: 600;
  color: #e6edf3;
  line-height: 1.4;
  word-break: break-word;
}
.tdm-close-btn {
  background: none;
  border: none;
  color: #484f58;
  cursor: pointer;
  font-size: 14px;
  line-height: 1;
  padding: 2px 4px;
  flex-shrink: 0;
  transition: color 0.15s;
  border-radius: 3px;
}
.tdm-close-btn:hover { color: #c9d1d9; background: #21262d; }

.tdm-divider { height: 1px; background: #21262d; flex-shrink: 0; }

/* Meta row */
.tdm-meta-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 20px;
  padding: 10px 16px;
  border-bottom: 1px solid #161b22;
  flex-shrink: 0;
}
.tdm-meta-item {
  display: flex;
  align-items: center;
  gap: 6px;
}
.tdm-meta-label {
  font-size: 10px;
  color: #484f58;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-weight: 600;
}

/* Badges */
.tdm-status-badge {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 10px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 3px;
  letter-spacing: 0.06em;
}
.tdm-status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}
.tdm-status-done    { background: #122211; color: #3fb950; }
.tdm-status-done    .tdm-status-dot { background: #3fb950; }
.tdm-status-active  { background: #0d1f38; color: #58a6ff; }
.tdm-status-active  .tdm-status-dot { background: #58a6ff; animation: tdm-pulse 1.2s ease-in-out infinite; }
.tdm-status-pending { background: #1a1e24; color: #8b949e; }
.tdm-status-pending .tdm-status-dot { background: #484f58; }
.tdm-status-failed  { background: #2d1515; color: #f78166; }
.tdm-status-failed  .tdm-status-dot { background: #f78166; }

.tdm-priority-badge {
  font-size: 10px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 3px;
  letter-spacing: 0.06em;
}
.tdm-priority-critical { background: #6e0909; color: #ffa198; }
.tdm-priority-high     { background: #5a2a05; color: #ffa657; }
.tdm-priority-medium   { background: #1e2128; color: #8b949e; }
.tdm-priority-low      { background: #161b22; color: #484f58; }

.tdm-value-badge {
  font-size: 10px;
  padding: 2px 6px;
  border-radius: 3px;
  background: #1e2128;
  color: #8b949e;
  letter-spacing: 0.04em;
}
.tdm-agent-badge {
  background: #12232a;
  color: #39d0d8;
  font-family: ui-monospace, monospace;
}
.tdm-link-badge {
  font-size: 10px;
  padding: 2px 6px;
  border-radius: 3px;
  background: #0d1f38;
  color: #58a6ff;
  cursor: pointer;
  letter-spacing: 0.04em;
}
.tdm-link-badge:hover { background: #1a3050; }

/* Scrollable body */
.tdm-body {
  flex: 1;
  overflow-y: auto;
  padding: 4px 0 8px;
}
.tdm-body::-webkit-scrollbar { width: 4px; }
.tdm-body::-webkit-scrollbar-track { background: transparent; }
.tdm-body::-webkit-scrollbar-thumb { background: #21262d; border-radius: 2px; }

/* Sections */
.tdm-section {
  padding: 10px 16px;
  border-bottom: 1px solid #161b22;
}
.tdm-section:last-child { border-bottom: none; }

.tdm-section-label {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.12em;
  color: #484f58;
  text-transform: uppercase;
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.tdm-section-count {
  font-weight: 400;
  letter-spacing: 0;
  color: #8b949e;
  text-transform: none;
}
.tdm-section-pct { color: #484f58; }

/* Description */
.tdm-description {
  color: #c9d1d9;
  line-height: 1.6;
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
}

/* Acceptance criteria */
.tdm-ac-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.tdm-ac-item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 4px 6px;
  border-radius: 4px;
}
.tdm-ac-done    { background: #0a1a10; }
.tdm-ac-pending { background: transparent; }
.tdm-ac-active  { background: #071525; }
.tdm-ac-failed  { background: #1a0a0a; }

.tdm-ac-icon {
  flex-shrink: 0;
  font-size: 12px;
  width: 14px;
  text-align: center;
  margin-top: 1px;
}
.tdm-ac-done    .tdm-ac-icon { color: #3fb950; }
.tdm-ac-pending .tdm-ac-icon { color: #484f58; }
.tdm-ac-active  .tdm-ac-icon { color: #58a6ff; }
.tdm-ac-failed  .tdm-ac-icon { color: #f78166; }

.tdm-ac-text {
  color: #c9d1d9;
  line-height: 1.5;
}
.tdm-ac-done .tdm-ac-text  { color: #3fb950; }
.tdm-ac-pending .tdm-ac-text { color: #8b949e; }

/* Children / subtasks */
.tdm-child-progress-bar {
  height: 4px;
  background: #21262d;
  border-radius: 2px;
  overflow: hidden;
  margin-bottom: 8px;
}
.tdm-child-fill {
  height: 100%;
  border-radius: 2px;
  transition: width 0.4s ease;
}
.tdm-fill-complete { background: #3fb950; }
.tdm-fill-high     { background: #3fb950; }
.tdm-fill-mid      { background: #d29922; }
.tdm-fill-low      { background: #484f58; }

.tdm-children-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.tdm-child-item {
  display: flex;
  align-items: baseline;
  gap: 6px;
  padding: 3px 6px;
  border-radius: 3px;
  cursor: pointer;
  transition: background 0.1s;
}
.tdm-child-item:hover { background: #1a1e24; }
.tdm-child-icon {
  font-size: 11px;
  width: 14px;
  text-align: center;
  flex-shrink: 0;
}
.tdm-child-done    .tdm-child-icon { color: #3fb950; }
.tdm-child-active  .tdm-child-icon { color: #58a6ff; }
.tdm-child-failed  .tdm-child-icon { color: #f78166; }
.tdm-child-pending .tdm-child-icon { color: #484f58; }
.tdm-child-id {
  font-size: 10px;
  color: #484f58;
  flex-shrink: 0;
}
.tdm-child-title {
  font-size: 11px;
  color: #c9d1d9;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.tdm-child-done .tdm-child-title   { color: #8b949e; opacity: 0.7; }
.tdm-child-pending .tdm-child-title { color: #8b949e; }

/* Notes */
.tdm-notes-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.tdm-note-item {
  padding: 6px 8px;
  background: #0d1117;
  border-left: 2px solid #21262d;
  border-radius: 0 3px 3px 0;
}
.tdm-note-text {
  color: #8b949e;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 11px;
}

/* GitHub */
.tdm-github-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  background: #0d1117;
  border: 1px solid #21262d;
  border-radius: 4px;
}
.tdm-github-icon { font-size: 14px; flex-shrink: 0; }
.tdm-github-text {
  flex: 1;
  color: #8b949e;
  font-size: 11px;
  font-family: ui-monospace, monospace;
}
.tdm-github-link {
  color: #58a6ff;
  text-decoration: none;
  font-size: 11px;
  flex-shrink: 0;
  padding: 2px 6px;
  border-radius: 3px;
  background: #0d1f38;
  border: 1px solid #1a3050;
  transition: background 0.15s;
}
.tdm-github-link:hover { background: #1a3050; }

/* Labels */
.tdm-labels-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.tdm-label-chip {
  font-size: 10px;
  padding: 2px 8px;
  border-radius: 20px;
  background: #21262d;
  color: #8b949e;
  border: 1px solid #30363d;
  letter-spacing: 0.04em;
}

/* Transitions */
.tdm-fade-enter-active,
.tdm-fade-leave-active { transition: opacity 0.2s ease; }
.tdm-fade-enter-from,
.tdm-fade-leave-to { opacity: 0; }

.tdm-scale-enter-active { transition: transform 0.18s ease, opacity 0.18s ease; }
.tdm-scale-leave-active { transition: transform 0.14s ease, opacity 0.14s ease; }
.tdm-scale-enter-from  { transform: scale(0.95) translateY(-8px); opacity: 0; }
.tdm-scale-leave-to    { transform: scale(0.97) translateY(4px); opacity: 0; }

/* Animations */
@keyframes tdm-spin {
  to { transform: rotate(360deg); }
}
@keyframes tdm-pulse {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0.3; }
}

/* Action bar */
.tdm-action-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  border-top: 1px solid #21262d;
  flex-shrink: 0;
  background: #0d1117;
}

.tdm-action-btn {
  font-family: ui-monospace, "Cascadia Code", Menlo, Consolas, monospace;
  font-size: 11px;
  background: #1a1a1a;
  color: #8b949e;
  border: 1px solid #30363d;
  border-radius: 20px;
  padding: 4px 10px;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s, background 0.15s;
  white-space: nowrap;
}
.tdm-action-btn:hover:not(:disabled) { border-color: #39d0d8; color: #39d0d8; }
.tdm-action-btn:disabled { opacity: 0.45; cursor: not-allowed; }

.tdm-action-note:hover:not(:disabled)  { border-color: #58a6ff; color: #58a6ff; }
.tdm-action-queue:hover:not(:disabled) { border-color: #3fb950; color: #3fb950; }
.tdm-action-submit:hover:not(:disabled) { background: #0d2910; border-color: #3fb950; color: #3fb950; }
.tdm-action-cancel:hover:not(:disabled) { border-color: #484f58; color: #484f58; }

.tdm-action-status {
  appearance: none;
  -webkit-appearance: none;
  padding-right: 12px;
  cursor: pointer;
}

/* Note compose area */
.tdm-note-compose {
  padding: 8px 16px 10px;
  border-top: 1px solid #161b22;
  background: #0d1117;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.tdm-note-textarea {
  width: 100%;
  box-sizing: border-box;
  background: #161b22;
  color: #c9d1d9;
  border: 1px solid #30363d;
  border-radius: 4px;
  padding: 8px;
  font-family: ui-monospace, "Cascadia Code", Menlo, Consolas, monospace;
  font-size: 11px;
  resize: vertical;
  min-height: 56px;
  transition: border-color 0.15s;
}
.tdm-note-textarea:focus { outline: none; border-color: #39d0d8; }
.tdm-note-textarea:disabled { opacity: 0.5; }

.tdm-note-compose-actions {
  display: flex;
  gap: 6px;
  justify-content: flex-end;
}

/* Toast */
.tdm-toast {
  position: fixed;
  top: 16px;
  right: 16px;
  z-index: 2000;
  font-family: ui-monospace, "Cascadia Code", Menlo, Consolas, monospace;
  font-size: 11px;
  padding: 8px 14px;
  border-radius: 4px;
  border: 1px solid;
  box-shadow: 0 4px 16px rgba(0,0,0,0.5);
  pointer-events: none;
}
.tdm-toast-ok  { background: #0a1a10; border-color: #3fb950; color: #3fb950; }
.tdm-toast-err { background: #2d1515; border-color: #f78166; color: #f78166; }

.tdm-toast-slide-enter-active { transition: opacity 0.2s ease, transform 0.2s ease; }
.tdm-toast-slide-leave-active { transition: opacity 0.3s ease, transform 0.3s ease; }
.tdm-toast-slide-enter-from  { opacity: 0; transform: translateY(-8px); }
.tdm-toast-slide-leave-to    { opacity: 0; transform: translateY(-4px); }
</style>
