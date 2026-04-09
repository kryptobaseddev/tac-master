<script setup lang="ts">
/**
 * RunDetailsPanel — right-panel showing metadata for the selected run.
 *
 * Includes a Retry button that appears only when the underlying tac-master
 * run status is 'failed' or 'aborted', allowing operators to re-queue the
 * issue without touching SQLite manually.
 *
 * @task T012
 * @epic T004
 * @why Operators previously needed manual SQLite edits to retry a failed run;
 *      this button calls POST /api/ops/retry-issue which uses the same
 *      guarded service method as the CLI.
 * @what Shows a Retry button for failed/aborted runs; posts to the backend
 *      retry endpoint and displays success/error feedback inline.
 */
import { computed, ref } from "vue";
import { useOrchestratorStore } from "../stores/orchestratorStore";
import type { EventStreamEntry, Agent } from "../types";

const store = useOrchestratorStore();

const agent = computed<Agent | null>(() => store.selectedAgent);

const agentEvents = computed<EventStreamEntry[]>(() => {
  if (!agent.value) return [];
  return store.eventStreamEntries.filter((e) => e.agentId === agent.value!.id);
});

const phaseCounts = computed(() => {
  const out: Record<string, number> = {};
  for (const e of agentEvents.value) {
    const phase = String(e.metadata?.phase ?? e.eventType ?? "other");
    out[phase] = (out[phase] || 0) + 1;
  }
  return out;
});

const repoSlug = computed(() => agent.value?.metadata?.repo_slug ?? "");
const repoUrl = computed(() => agent.value?.metadata?.repo_url ?? "");
const issueNumber = computed(() => agent.value?.metadata?.issue_number ?? null);

// The raw tac-master run status stored in metadata by orchestratorStore.ts
const runStatus = computed(() => (agent.value?.metadata?.run_status as string | undefined) ?? null);

// Show Retry button only for failed/aborted runs that have a repo+issue
const canRetry = computed(
  () =>
    (runStatus.value === "failed" || runStatus.value === "aborted") &&
    !!repoUrl.value &&
    issueNumber.value != null,
);

// Retry button UI state
const retryPending = ref(false);
const retryMessage = ref<{ ok: boolean; text: string } | null>(null);

/**
 * Post a retry request to the dashboard backend.  The backend shells out to
 * orchestrator/ops.py which performs the guarded status reset.
 */
async function handleRetry(): Promise<void> {
  if (!canRetry.value || retryPending.value) return;
  retryMessage.value = null;
  retryPending.value = true;
  try {
    const resp = await fetch("/api/ops/retry-issue", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        issue_number: issueNumber.value,
        repo_url: repoUrl.value,
      }),
    });
    const data = (await resp.json()) as { ok: boolean; message?: string; error?: string };
    retryMessage.value = {
      ok: data.ok,
      text: data.message ?? data.error ?? (data.ok ? "Retry queued." : "Retry failed."),
    };
  } catch (err: any) {
    retryMessage.value = { ok: false, text: String(err?.message ?? err) };
  } finally {
    retryPending.value = false;
  }
}

function fmtTs(ts: string | Date | undefined): string {
  if (!ts) return "—";
  const d = typeof ts === "string" ? new Date(ts) : ts;
  return d.toLocaleString();
}

function fmtDuration(a?: Agent | null): string {
  if (!a) return "—";
  const start = a.metadata?.started_at_unix as number | undefined;
  const end = (a.metadata?.ended_at_unix as number | undefined) ?? Math.floor(Date.now() / 1000);
  if (!start) return "—";
  const d = end - start;
  if (d < 60) return `${d}s`;
  if (d < 3600) return `${Math.floor(d / 60)}m ${d % 60}s`;
  return `${Math.floor(d / 3600)}h ${Math.floor((d % 3600) / 60)}m`;
}

function statusColor(s: string | null): string {
  return (
    {
      executing: "#3b82f6",
      waiting: "#f59e0b",
      idle: "#9ca3af",
      complete: "#10b981",
      blocked: "#ef4444",
    }[s ?? "idle"] ?? "#9ca3af"
  );
}
</script>

<template>
  <aside class="run-details">
    <header class="rd-header">
      <h2 class="rd-title">Run Details</h2>
      <span v-if="agent" class="rd-connected" />
    </header>

    <div v-if="!agent" class="rd-empty">
      <div class="rd-empty-icon">◇</div>
      <p>Select a run from the left sidebar to view its details.</p>
    </div>

    <div v-else class="rd-body">
      <!-- Summary card -->
      <section class="rd-card">
        <div class="rd-card-head">
          <span
            class="rd-dot"
            :style="{ background: statusColor(agent.status) }"
          />
          <span class="rd-name">{{ agent.name }}</span>
        </div>
        <div class="rd-meta">
          <div><span class="k">adw_id</span><span class="v">{{ agent.id }}</span></div>
          <div><span class="k">status</span><span class="v">{{ agent.status }}</span></div>
          <div><span class="k">workflow</span><span class="v">{{ agent.adw_step }}</span></div>
          <div><span class="k">model</span><span class="v">{{ agent.model }}</span></div>
          <div><span class="k">started</span><span class="v">{{ fmtTs(agent.created_at) }}</span></div>
          <div><span class="k">duration</span><span class="v">{{ fmtDuration(agent) }}</span></div>
          <div v-if="agent.metadata?.pid"><span class="k">pid</span><span class="v">{{ agent.metadata.pid }}</span></div>
        </div>
      </section>

      <!-- Retry action — only visible for failed / aborted runs -->
      <section v-if="canRetry" class="rd-card rd-card-action">
        <div class="rd-card-head">
          <span class="rd-icon rd-icon-warn">!</span>
          <span class="rd-name">Run {{ runStatus }}</span>
        </div>
        <div class="rd-retry-body">
          <p class="rd-retry-hint">
            This run ended in <strong>{{ runStatus }}</strong>. Retrying will reset the issue
            status and re-queue it for the next poll cycle.
          </p>
          <button
            class="rd-retry-btn"
            :disabled="retryPending"
            @click="handleRetry"
          >
            {{ retryPending ? "Retrying..." : "Retry Issue" }}
          </button>
          <p v-if="retryMessage" :class="['rd-retry-msg', retryMessage.ok ? 'ok' : 'err']">
            {{ retryMessage.text }}
          </p>
        </div>
      </section>

      <!-- Repo card -->
      <section class="rd-card">
        <div class="rd-card-head">
          <span class="rd-icon">◎</span>
          <span class="rd-name">Repository</span>
        </div>
        <div class="rd-meta">
          <div>
            <span class="k">repo</span>
            <a :href="repoUrl" target="_blank" class="v link">{{ repoSlug }}</a>
          </div>
          <div v-if="issueNumber">
            <span class="k">issue</span>
            <a :href="`${repoUrl}/issues/${issueNumber}`" target="_blank" class="v link">
              #{{ issueNumber }}
            </a>
          </div>
          <div v-if="agent.working_dir">
            <span class="k">worktree</span>
            <span class="v mono">{{ agent.working_dir }}</span>
          </div>
        </div>
      </section>

      <!-- Tokens / cost -->
      <section class="rd-card">
        <div class="rd-card-head">
          <span class="rd-icon">$</span>
          <span class="rd-name">Cost &amp; Tokens</span>
        </div>
        <div class="rd-meta">
          <div><span class="k">input</span><span class="v">{{ agent.input_tokens.toLocaleString() }}</span></div>
          <div><span class="k">output</span><span class="v">{{ agent.output_tokens.toLocaleString() }}</span></div>
          <div><span class="k">total</span><span class="v">${{ (agent.total_cost ?? 0).toFixed(4) }}</span></div>
        </div>
      </section>

      <!-- Phase activity -->
      <section class="rd-card">
        <div class="rd-card-head">
          <span class="rd-icon">◈</span>
          <span class="rd-name">Activity by phase ({{ agentEvents.length }} events)</span>
        </div>
        <div v-if="Object.keys(phaseCounts).length === 0" class="rd-empty-inline">
          No events yet for this run.
        </div>
        <div v-else class="rd-phases">
          <div v-for="(count, phase) in phaseCounts" :key="phase" class="rd-phase">
            <span class="rd-phase-name">{{ phase }}</span>
            <span class="rd-phase-count">{{ count }}</span>
          </div>
        </div>
      </section>
    </div>
  </aside>
</template>

<style scoped>
.run-details {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--color-bg-secondary, #1a1a1a);
  border-left: 1px solid var(--color-border, #2a2a2a);
  color: var(--color-text-primary, #e4e7eb);
  font-family: var(--font-mono, ui-monospace, monospace);
  overflow: hidden;
}
.rd-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 18px;
  border-bottom: 1px solid var(--color-border, #2a2a2a);
  background: var(--color-bg-tertiary, #12171e);
}
.rd-title {
  font-size: 13px;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: #a855f7;
  margin: 0;
}
.rd-connected {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #10b981;
  box-shadow: 0 0 8px #10b98188;
}
.rd-empty {
  padding: 60px 24px;
  text-align: center;
  color: #6b7280;
}
.rd-empty-icon {
  font-size: 40px;
  opacity: 0.3;
  margin-bottom: 12px;
}
.rd-body {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.rd-card {
  background: var(--color-bg-primary, #0a0a0a);
  border: 1px solid var(--color-border, #2a2a2a);
  border-radius: 6px;
  padding: 12px 14px;
}
.rd-card-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-bottom: 8px;
  margin-bottom: 10px;
  border-bottom: 1px solid var(--color-border, #2a2a2a);
}
.rd-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  display: inline-block;
}
.rd-icon {
  font-size: 13px;
  color: #a855f7;
  font-weight: 700;
}
.rd-name {
  font-size: 12px;
  font-weight: 700;
  color: #f5f7fa;
}
.rd-meta {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.rd-meta > div {
  display: flex;
  align-items: baseline;
  gap: 12px;
  font-size: 11px;
}
.rd-meta .k {
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #6b7280;
  min-width: 72px;
}
.rd-meta .v {
  color: #e4e7eb;
  word-break: break-all;
}
.rd-meta .v.mono {
  font-family: inherit;
  font-size: 10px;
}
.rd-meta .v.link {
  color: #3b82f6;
  text-decoration: none;
}
.rd-meta .v.link:hover {
  text-decoration: underline;
}
.rd-phases {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
}
.rd-phase {
  display: flex;
  justify-content: space-between;
  background: #12171e;
  padding: 6px 10px;
  border-radius: 4px;
  font-size: 11px;
}
.rd-phase-name {
  color: #e4e7eb;
}
.rd-phase-count {
  color: #a855f7;
  font-weight: 700;
}
.rd-empty-inline {
  color: #6b7280;
  font-size: 11px;
  font-style: italic;
}
.rd-card-action {
  border-color: #7f1d1d;
  background: #1a0a0a;
}
.rd-icon-warn {
  color: #ef4444;
  font-size: 13px;
  font-weight: 900;
}
.rd-retry-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.rd-retry-hint {
  font-size: 11px;
  color: #9ca3af;
  margin: 0;
  line-height: 1.5;
}
.rd-retry-hint strong {
  color: #ef4444;
}
.rd-retry-btn {
  align-self: flex-start;
  background: #ef4444;
  color: #fff;
  border: none;
  border-radius: 4px;
  padding: 6px 14px;
  font-size: 11px;
  font-family: inherit;
  font-weight: 700;
  letter-spacing: 0.04em;
  cursor: pointer;
  transition: background 0.15s;
}
.rd-retry-btn:hover:not(:disabled) {
  background: #dc2626;
}
.rd-retry-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.rd-retry-msg {
  font-size: 11px;
  margin: 0;
  padding: 6px 10px;
  border-radius: 4px;
}
.rd-retry-msg.ok {
  background: #052e16;
  color: #4ade80;
  border: 1px solid #166534;
}
.rd-retry-msg.err {
  background: #1c0606;
  color: #f87171;
  border: 1px solid #7f1d1d;
}
</style>
