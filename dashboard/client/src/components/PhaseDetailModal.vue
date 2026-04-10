<template>
  <Teleport to="body">
    <Transition name="pdm-fade">
      <div
        v-if="visible"
        class="pdm-backdrop"
        @click.self="emit('close')"
        tabindex="-1"
        ref="backdropRef"
      >
        <Transition name="pdm-scale">
          <div
            v-if="visible"
            class="pdm-card"
            role="dialog"
            aria-modal="true"
            :aria-label="`Phase detail: ${phaseLabel}`"
          >
            <!-- Header -->
            <div class="pdm-header">
              <div class="pdm-header-left">
                <span class="pdm-phase-name">{{ phaseLabel }}</span>
                <span class="pdm-sep">·</span>
                <span class="pdm-phase-sub">PHASE DETAIL</span>
              </div>
              <button class="pdm-close-btn" @click="emit('close')" title="Close (Esc)">✕</button>
            </div>

            <div class="pdm-divider"></div>

            <!-- Loading spinner -->
            <div v-if="loading" class="pdm-loading">
              <span class="pdm-spinner"></span>
              <span class="pdm-loading-text">Loading phase data...</span>
            </div>

            <!-- Error -->
            <div v-else-if="error" class="pdm-error">
              <span class="pdm-error-icon">&#9888;</span>
              <span>{{ error }}</span>
            </div>

            <!-- Content -->
            <template v-else-if="summary">
              <!-- Meta row: status + duration + agent -->
              <div class="pdm-meta-row">
                <div class="pdm-meta-item">
                  <span class="pdm-meta-label">Status</span>
                  <span class="pdm-status-badge" :class="`pdm-status-${normStatus(summary.status)}`">
                    <span class="pdm-status-dot"></span>
                    {{ statusLabel(summary.status) }}
                  </span>
                </div>

                <div class="pdm-meta-item" v-if="summary.duration_seconds != null">
                  <span class="pdm-meta-label">Duration</span>
                  <span class="pdm-value-badge">{{ formatDuration(summary.duration_seconds) }}</span>
                </div>

                <div class="pdm-meta-item" v-if="adwId">
                  <span class="pdm-meta-label">Agent</span>
                  <span class="pdm-agent-badge" :title="adwId">{{ shortAdwId(adwId) }}</span>
                </div>

                <div class="pdm-meta-item" v-if="summary.total_events > 0">
                  <span class="pdm-meta-label">Events</span>
                  <span class="pdm-value-badge">{{ summary.total_events }}</span>
                </div>
              </div>

              <!-- Scrollable body -->
              <div class="pdm-body">

                <!-- Event breakdown -->
                <section v-if="summary.total_events > 0" class="pdm-section">
                  <div class="pdm-section-label">EVENT BREAKDOWN</div>
                  <div class="pdm-event-counts">
                    <div
                      v-for="(count, evType) in summary.event_counts"
                      :key="evType"
                      class="pdm-event-chip"
                    >
                      <span class="pdm-event-type">{{ evType }}</span>
                      <span class="pdm-event-count">{{ count }}</span>
                    </div>
                  </div>
                </section>

                <!-- Output artifacts -->
                <section v-if="hasArtifacts" class="pdm-section">
                  <div class="pdm-section-label">OUTPUT ARTIFACTS</div>
                  <div class="pdm-artifacts">

                    <!-- Classify -->
                    <div v-if="summary.artifacts.classified_as" class="pdm-artifact-row">
                      <span class="pdm-artifact-icon">&#128193;</span>
                      <div class="pdm-artifact-detail">
                        <span class="pdm-artifact-key">Classification</span>
                        <span class="pdm-artifact-val">{{ summary.artifacts.classified_as }}</span>
                      </div>
                    </div>

                    <!-- Plan: spec file -->
                    <div v-if="summary.artifacts.spec_file" class="pdm-artifact-row">
                      <span class="pdm-artifact-icon">&#128196;</span>
                      <div class="pdm-artifact-detail">
                        <span class="pdm-artifact-key">Spec file</span>
                        <span class="pdm-artifact-val pdm-mono">{{ summary.artifacts.spec_file }}</span>
                      </div>
                    </div>

                    <!-- Build: branch + commit info -->
                    <div v-if="summary.artifacts.branch" class="pdm-artifact-row">
                      <span class="pdm-artifact-icon">&#127968;</span>
                      <div class="pdm-artifact-detail">
                        <span class="pdm-artifact-key">Branch</span>
                        <span class="pdm-artifact-val pdm-mono">{{ summary.artifacts.branch }}</span>
                      </div>
                    </div>
                    <div v-if="summary.artifacts.commit_message" class="pdm-artifact-row">
                      <span class="pdm-artifact-icon">&#128190;</span>
                      <div class="pdm-artifact-detail">
                        <span class="pdm-artifact-key">Commit</span>
                        <span class="pdm-artifact-val">{{ summary.artifacts.commit_message }}</span>
                      </div>
                    </div>
                    <div v-if="summary.artifacts.files_changed != null" class="pdm-artifact-row">
                      <span class="pdm-artifact-icon">&#128209;</span>
                      <div class="pdm-artifact-detail">
                        <span class="pdm-artifact-key">Files changed</span>
                        <span class="pdm-artifact-val">{{ summary.artifacts.files_changed }}</span>
                      </div>
                    </div>

                    <!-- Test: pass/fail -->
                    <div v-if="summary.artifacts.passed != null || summary.artifacts.failed != null" class="pdm-artifact-row">
                      <span class="pdm-artifact-icon">&#9989;</span>
                      <div class="pdm-artifact-detail">
                        <span class="pdm-artifact-key">Tests</span>
                        <span class="pdm-artifact-val">
                          <span class="pdm-pass">{{ summary.artifacts.passed ?? 0 }} passed</span>
                          <span v-if="(summary.artifacts.failed ?? 0) > 0"> · <span class="pdm-fail">{{ summary.artifacts.failed }} failed</span></span>
                          <span v-if="(summary.artifacts.auto_fixed ?? 0) > 0"> · <span class="pdm-fixed">{{ summary.artifacts.auto_fixed }} auto-fixed</span></span>
                        </span>
                      </div>
                    </div>

                    <!-- Review: status -->
                    <div v-if="summary.artifacts.review_status" class="pdm-artifact-row">
                      <span class="pdm-artifact-icon">&#128269;</span>
                      <div class="pdm-artifact-detail">
                        <span class="pdm-artifact-key">Review</span>
                        <span class="pdm-artifact-val">{{ summary.artifacts.review_status }}</span>
                      </div>
                    </div>

                    <!-- Document: committed -->
                    <div v-if="summary.artifacts.docs_committed != null" class="pdm-artifact-row">
                      <span class="pdm-artifact-icon">&#128220;</span>
                      <div class="pdm-artifact-detail">
                        <span class="pdm-artifact-key">Docs committed</span>
                        <span class="pdm-artifact-val" :class="summary.artifacts.docs_committed ? 'pdm-pass' : 'pdm-fail'">
                          {{ summary.artifacts.docs_committed ? "Yes" : "No" }}
                        </span>
                      </div>
                    </div>

                    <!-- Ship: PR -->
                    <div v-if="summary.artifacts.pr_number != null" class="pdm-artifact-row">
                      <span class="pdm-artifact-icon">&#128279;</span>
                      <div class="pdm-artifact-detail">
                        <span class="pdm-artifact-key">Pull Request</span>
                        <span class="pdm-artifact-val">
                          <a
                            v-if="summary.artifacts.pr_url"
                            :href="summary.artifacts.pr_url"
                            target="_blank"
                            rel="noopener noreferrer"
                            class="pdm-pr-link"
                          >#{{ summary.artifacts.pr_number }} ↗</a>
                          <span v-else>#{{ summary.artifacts.pr_number }}</span>
                          <span v-if="summary.artifacts.merged" class="pdm-merged-badge">MERGED</span>
                        </span>
                      </div>
                    </div>

                    <!-- Reflect: lesson -->
                    <div v-if="summary.artifacts.lesson_summary" class="pdm-artifact-row">
                      <span class="pdm-artifact-icon">&#128218;</span>
                      <div class="pdm-artifact-detail">
                        <span class="pdm-artifact-key">Lesson</span>
                        <span class="pdm-artifact-val">{{ summary.artifacts.lesson_summary }}</span>
                      </div>
                    </div>

                  </div>
                </section>

                <!-- Timestamps -->
                <section v-if="summary.first_event_ts" class="pdm-section">
                  <div class="pdm-section-label">TIMING</div>
                  <div class="pdm-timing-row">
                    <div class="pdm-timing-item">
                      <span class="pdm-timing-label">Started</span>
                      <span class="pdm-timing-val">{{ fmtTimestamp(summary.first_event_ts) }}</span>
                    </div>
                    <div v-if="summary.last_event_ts" class="pdm-timing-item">
                      <span class="pdm-timing-label">Last event</span>
                      <span class="pdm-timing-val">{{ fmtTimestamp(summary.last_event_ts) }}</span>
                    </div>
                  </div>
                </section>

                <!-- No data placeholder -->
                <div v-if="!summary.total_events && !hasArtifacts" class="pdm-empty">
                  No event data recorded for this phase yet.
                </div>

              </div>
            </template>
          </div>
        </Transition>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
/**
 * PhaseDetailModal — shows phase status, duration, artifacts, and event counts
 * when a user clicks a phase node in PipelineFlow.
 *
 * @task T055
 * @epic T051
 */
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from "vue";

const props = defineProps<{
  visible: boolean;
  adwId: string | null;
  phase: string | null;
}>();

const emit = defineEmits<{
  (e: "close"): void;
}>();

// ── Types ─────────────────────────────────────────────────────────────────────

interface PhaseArtifacts {
  classified_as?: string;
  spec_file?: string;
  branch?: string;
  commits?: number;
  files_changed?: number;
  commit_message?: string;
  passed?: number;
  failed?: number;
  auto_fixed?: number;
  review_status?: string;
  docs_committed?: boolean;
  pr_number?: number;
  pr_url?: string;
  merged?: boolean;
  lesson_written?: boolean;
  lesson_summary?: string;
}

interface PhaseSummary {
  phase: string;
  adw_id: string;
  status: string;
  duration_seconds: number | null;
  first_event_ts: number | null;
  last_event_ts: number | null;
  event_counts: Record<string, number>;
  total_events: number;
  artifacts: PhaseArtifacts;
}

// ── State ─────────────────────────────────────────────────────────────────────

const summary = ref<PhaseSummary | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);
const backdropRef = ref<HTMLElement | null>(null);

// ── Derived ───────────────────────────────────────────────────────────────────

const PHASE_LABELS: Record<string, string> = {
  classify_iso: "CLASSIFY",
  plan_iso:     "PLAN",
  build_iso:    "BUILD",
  test_iso:     "TEST",
  review_iso:   "REVIEW",
  document_iso: "DOCUMENT",
  ship_iso:     "SHIP",
  reflect_iso:  "REFLECT",
};

const phaseLabel = computed(() => {
  if (!props.phase) return "";
  return PHASE_LABELS[props.phase] ?? props.phase.replace("_iso", "").toUpperCase();
});

const hasArtifacts = computed(() => {
  if (!summary.value) return false;
  const a = summary.value.artifacts;
  return Object.values(a).some((v) => v != null && v !== "");
});

// ── Fetch ─────────────────────────────────────────────────────────────────────

async function fetchSummary(): Promise<void> {
  if (!props.adwId || !props.phase) return;
  loading.value = true;
  error.value = null;
  summary.value = null;
  try {
    const resp = await fetch(
      `/api/runs/${encodeURIComponent(props.adwId)}/phase/${encodeURIComponent(props.phase)}/summary`,
    );
    if (!resp.ok) throw new Error(`Server responded with ${resp.status}`);
    summary.value = (await resp.json()) as PhaseSummary;
  } catch (e: any) {
    error.value = String(e?.message ?? e);
  } finally {
    loading.value = false;
  }
}

watch(
  () => [props.visible, props.adwId, props.phase] as const,
  ([vis]) => {
    if (vis) fetchSummary();
    else {
      summary.value = null;
      error.value = null;
    }
  },
  { immediate: false },
);

// Focus backdrop on open for Escape key support
watch(
  () => props.visible,
  async (val) => {
    if (val) {
      await nextTick();
      backdropRef.value?.focus();
    }
  },
);

function handleKeydown(e: KeyboardEvent): void {
  if (e.key === "Escape" && props.visible) emit("close");
}
onMounted(() => document.addEventListener("keydown", handleKeydown));
onUnmounted(() => document.removeEventListener("keydown", handleKeydown));

// ── Helpers ───────────────────────────────────────────────────────────────────

function normStatus(s: string): string {
  const sl = (s ?? "").toLowerCase();
  if (sl === "succeeded" || sl === "completed" || sl === "done") return "done";
  if (sl === "running" || sl === "active") return "active";
  if (sl === "failed" || sl === "aborted") return "failed";
  return "pending";
}

function statusLabel(s: string): string {
  const n = normStatus(s);
  if (n === "done")    return "SUCCEEDED";
  if (n === "active")  return "RUNNING";
  if (n === "failed")  return "FAILED";
  return "PENDING";
}

/**
 * Format seconds into human-readable duration.
 */
function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  if (m < 60) return s > 0 ? `${m}m ${s}s` : `${m}m`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  return rm > 0 ? `${h}h ${rm}m` : `${h}h`;
}

function shortAdwId(id: string): string {
  // Show last 8 chars for brevity (full id is usually a UUID)
  return id.length > 12 ? `…${id.slice(-12)}` : id;
}

function fmtTimestamp(ts: number): string {
  // Detect ms vs seconds epoch
  const ms = ts > 1e12 ? ts : ts * 1000;
  return new Date(ms).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}
</script>

<style scoped>
/* ── Backdrop ─────────────────────────────────────────────────────────────── */
.pdm-backdrop {
  position: fixed;
  inset: 0;
  z-index: 1100;
  background: rgba(0, 0, 0, 0.82);
  backdrop-filter: blur(3px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  outline: none;
}

/* ── Card ─────────────────────────────────────────────────────────────────── */
.pdm-card {
  background: #111317;
  border: 1px solid #1e2229;
  border-radius: 8px;
  width: 100%;
  max-width: 580px;
  max-height: 82vh;
  display: flex;
  flex-direction: column;
  font-family: ui-monospace, "Cascadia Code", Menlo, Consolas, monospace;
  font-size: 12px;
  color: #c9d1d9;
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.6);
  overflow: hidden;
}

/* ── Header ───────────────────────────────────────────────────────────────── */
.pdm-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 16px 12px;
  flex-shrink: 0;
}
.pdm-header-left {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex: 1;
  min-width: 0;
}
.pdm-phase-name {
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 0.12em;
  color: #00d4aa;
}
.pdm-sep {
  color: #484f58;
  flex-shrink: 0;
}
.pdm-phase-sub {
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.1em;
  color: #484f58;
  text-transform: uppercase;
}
.pdm-close-btn {
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
.pdm-close-btn:hover { color: #c9d1d9; background: #21262d; }

.pdm-divider { height: 1px; background: #21262d; flex-shrink: 0; }

/* ── Loading / Error ──────────────────────────────────────────────────────── */
.pdm-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  color: #8b949e;
  padding: 32px;
  font-size: 12px;
}
.pdm-spinner {
  display: inline-block;
  width: 20px;
  height: 20px;
  border: 2px solid #21262d;
  border-top-color: #00d4aa;
  border-radius: 50%;
  animation: pdm-spin 0.8s linear infinite;
}
.pdm-loading-text { color: #484f58; }

.pdm-error {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 16px;
  color: #f78166;
  background: #1a0a0a;
  border-top: 1px solid #2d1515;
}
.pdm-error-icon { font-size: 16px; }

/* ── Meta row ─────────────────────────────────────────────────────────────── */
.pdm-meta-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 20px;
  padding: 10px 16px;
  border-bottom: 1px solid #161b22;
  flex-shrink: 0;
}
.pdm-meta-item {
  display: flex;
  align-items: center;
  gap: 6px;
}
.pdm-meta-label {
  font-size: 10px;
  color: #484f58;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-weight: 600;
}

/* Status badge */
.pdm-status-badge {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 10px;
  font-weight: 700;
  padding: 2px 7px;
  border-radius: 3px;
  letter-spacing: 0.06em;
}
.pdm-status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}
.pdm-status-done    { background: #122211; color: #3fb950; }
.pdm-status-done    .pdm-status-dot { background: #3fb950; }
.pdm-status-active  { background: #0a1e12; color: #00d4aa; }
.pdm-status-active  .pdm-status-dot { background: #00d4aa; animation: pdm-pulse 1.2s ease-in-out infinite; }
.pdm-status-pending { background: #1a1e24; color: #8b949e; }
.pdm-status-pending .pdm-status-dot { background: #484f58; }
.pdm-status-failed  { background: #2d1515; color: #f78166; }
.pdm-status-failed  .pdm-status-dot { background: #f78166; }

/* Value badges */
.pdm-value-badge {
  font-size: 10px;
  padding: 2px 7px;
  border-radius: 3px;
  background: #1e2128;
  color: #8b949e;
  letter-spacing: 0.03em;
}
.pdm-agent-badge {
  font-size: 10px;
  padding: 2px 7px;
  border-radius: 3px;
  background: #0a1e1a;
  color: #00d4aa;
  letter-spacing: 0.03em;
  font-family: ui-monospace, monospace;
}

/* ── Scrollable body ──────────────────────────────────────────────────────── */
.pdm-body {
  flex: 1;
  overflow-y: auto;
  padding: 4px 0 8px;
}
.pdm-body::-webkit-scrollbar { width: 4px; }
.pdm-body::-webkit-scrollbar-track { background: transparent; }
.pdm-body::-webkit-scrollbar-thumb { background: #21262d; border-radius: 2px; }

/* ── Sections ─────────────────────────────────────────────────────────────── */
.pdm-section {
  padding: 10px 16px;
  border-bottom: 1px solid #161b22;
}
.pdm-section:last-child { border-bottom: none; }
.pdm-section-label {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.12em;
  color: #484f58;
  text-transform: uppercase;
  margin-bottom: 8px;
}

/* ── Event counts ─────────────────────────────────────────────────────────── */
.pdm-event-counts {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.pdm-event-chip {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 3px 8px;
  border-radius: 4px;
  background: #1a1e24;
  border: 1px solid #21262d;
}
.pdm-event-type {
  font-size: 10px;
  color: #8b949e;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.pdm-event-count {
  font-size: 11px;
  font-weight: 700;
  color: #c9d1d9;
}

/* ── Artifacts ────────────────────────────────────────────────────────────── */
.pdm-artifacts {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.pdm-artifact-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 6px 8px;
  background: #0d1117;
  border: 1px solid #21262d;
  border-radius: 4px;
}
.pdm-artifact-icon {
  font-size: 14px;
  flex-shrink: 0;
  margin-top: 1px;
}
.pdm-artifact-detail {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
  min-width: 0;
}
.pdm-artifact-key {
  font-size: 10px;
  color: #484f58;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  font-weight: 600;
}
.pdm-artifact-val {
  font-size: 11px;
  color: #c9d1d9;
  word-break: break-word;
  line-height: 1.4;
}
.pdm-mono { font-family: ui-monospace, monospace; color: #8b949e; }

/* Pass / fail / fixed */
.pdm-pass  { color: #3fb950; }
.pdm-fail  { color: #f78166; }
.pdm-fixed { color: #d29922; }

/* PR link */
.pdm-pr-link {
  color: #58a6ff;
  text-decoration: none;
  padding: 1px 5px;
  border-radius: 3px;
  background: #0d1f38;
  border: 1px solid #1a3050;
  font-size: 11px;
}
.pdm-pr-link:hover { background: #1a3050; }

.pdm-merged-badge {
  display: inline-block;
  margin-left: 6px;
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.06em;
  padding: 1px 5px;
  border-radius: 3px;
  background: #1a103a;
  color: #a371f7;
  vertical-align: middle;
}

/* ── Timing ───────────────────────────────────────────────────────────────── */
.pdm-timing-row {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
}
.pdm-timing-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.pdm-timing-label {
  font-size: 10px;
  color: #484f58;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  font-weight: 600;
}
.pdm-timing-val {
  font-size: 11px;
  color: #8b949e;
}

/* ── Empty state ──────────────────────────────────────────────────────────── */
.pdm-empty {
  padding: 24px 16px;
  text-align: center;
  color: #484f58;
  font-size: 11px;
}

/* ── Transitions ──────────────────────────────────────────────────────────── */
.pdm-fade-enter-active,
.pdm-fade-leave-active { transition: opacity 0.2s ease; }
.pdm-fade-enter-from,
.pdm-fade-leave-to { opacity: 0; }

.pdm-scale-enter-active { transition: transform 0.18s ease, opacity 0.18s ease; }
.pdm-scale-leave-active { transition: transform 0.14s ease, opacity 0.14s ease; }
.pdm-scale-enter-from  { transform: scale(0.95) translateY(-8px); opacity: 0; }
.pdm-scale-leave-to    { transform: scale(0.97) translateY(4px); opacity: 0; }

/* ── Animations ───────────────────────────────────────────────────────────── */
@keyframes pdm-spin {
  to { transform: rotate(360deg); }
}
@keyframes pdm-pulse {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0.3; }
}
</style>
