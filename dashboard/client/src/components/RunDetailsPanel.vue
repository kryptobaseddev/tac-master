<script setup lang="ts">
import { computed } from "vue";
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
</style>
