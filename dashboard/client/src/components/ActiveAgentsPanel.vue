<template>
  <div class="active-agents-panel">
    <div class="panel-header">
      <span class="panel-title">AGENTS</span>
      <span class="status-badge" :class="runningAgents.length > 0 ? 'badge-online' : 'badge-offline'">
        <span class="badge-dot" :class="runningAgents.length > 0 ? 'dot-green' : 'dot-gray'" />
        {{ runningAgents.length > 0
          ? `${runningAgents.length} ONLINE`
          : displayAgents.length > 0
            ? `0 ONLINE · ${displayAgents.length} RECENT`
            : 'NO AGENTS' }}
      </span>
    </div>

    <div class="agent-list-body">
      <div v-if="displayAgents.length === 0" class="empty-state">
        <span class="empty-icon">&#9675;</span>
        <span class="empty-text">No agent runs found</span>
      </div>

      <div
        v-for="agent in displayAgents"
        :key="agent.adw_id"
        class="agent-row"
        :class="{
          selected: agent.adw_id === store.selectedAgentId,
          stalled: agentDotClass(agent) === 'dot-yellow',
        }"
        @click="store.selectAgent(agent.adw_id)"
      >
        <span
          class="status-dot"
          :class="agentDotClass(agent)"
          :title="agentDotTitle(agent)"
        />
        <div class="agent-info">
          <div class="agent-name-row">
            <span class="agent-name">{{ formatName(agent) }}</span>
            <span v-if="agent.cleoTaskId" class="cleo-badge">{{ agent.cleoTaskId }}</span>
          </div>
          <span class="agent-activity">{{ formatActivity(agent) }}</span>
        </div>
        <span
          class="agent-status-tag"
          :class="agentTagClass(agent)"
        >
          {{ agentStatusLabel(agent) }}
        </span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * ActiveAgentsPanel — shows ALL known agents (not just running ones).
 * Renamed from "ACTIVE AGENTS" to "AGENTS".
 *
 * Never empty: shows the most recent runs (up to 10) even when nothing
 * is currently executing. Status dots:
 *   green  = running
 *   yellow = stalled (running > 5 min with no recent events)
 *   gray   = completed / succeeded
 *   red    = failed / aborted
 *
 * @task T044
 * @epic T036
 */
import { computed } from "vue";
import { useOrchestratorStore } from "../stores/orchestratorStore";
import type { Agent } from "../types";

const store = useOrchestratorStore();

const STALL_MS = 5 * 60 * 1000; // 5 minutes
const MAX_DISPLAY = 10;

/** Show up to MAX_DISPLAY most-recent agents. */
const displayAgents = computed(() =>
  store.recentAgents.slice(0, MAX_DISPLAY).map((a) => ({
    ...a,
    adw_id: a.id,
    cleoTaskId: deriveCleoTaskId(a),
  })),
);

const runningAgents = computed(() => store.runningAgents);

/**
 * Attempt to derive a CLEO task ID from the agent's issue_number.
 * tac-master issues >= 1 are typically mapped T0XX — this is a best-effort
 * heuristic. If no mapping exists we return null.
 */
function deriveCleoTaskId(agent: Agent): string | null {
  const issueNum = agent.metadata?.issue_number as number | undefined;
  if (!issueNum || issueNum <= 0) return null;
  // Simple heuristic: pad to 3 digits
  return `T${String(issueNum).padStart(3, "0")}`;
}

function formatName(agent: Agent & { adw_id: string }): string {
  const shortId = agent.adw_id.slice(0, 8);
  const step = agent.adw_step ?? "Worker";
  const role = step.charAt(0).toUpperCase() + step.slice(1).split("_")[0];
  return `${role}-${shortId}`;
}

function formatActivity(agent: Agent & { adw_id: string }): string {
  const issueNum = agent.metadata?.issue_number ?? "?";
  const repoSlug = agent.metadata?.repo_slug ?? "";
  const runStatus = (agent.metadata?.run_status as string) ?? agent.adw_step ?? "processing";

  const events = store.eventStreamEntries.filter((e) => e.agentId === agent.adw_id);
  if (events.length > 0) {
    const last = events[events.length - 1];
    const phaseName = last.metadata?.phase ?? runStatus;
    return `Issue #${issueNum}${repoSlug ? ` (${repoSlug})` : ""} — ${phaseName}`;
  }
  return `Issue #${issueNum}${repoSlug ? ` (${repoSlug})` : ""} — ${runStatus}`;
}

function isStalled(agent: Agent): boolean {
  if (agent.status !== "executing") return false;
  const events = store.eventStreamEntries.filter((e) => e.agentId === agent.id);
  if (events.length === 0) {
    const startedAt = agent.metadata?.started_at_unix as number | undefined;
    if (!startedAt) return false;
    return Date.now() - startedAt * 1000 > STALL_MS;
  }
  const last = events[events.length - 1];
  const lastTs =
    typeof last.timestamp === "string"
      ? new Date(last.timestamp).getTime()
      : Number(last.timestamp);
  return Date.now() - lastTs > STALL_MS;
}

function agentDotClass(agent: Agent): string {
  const runStatus = (agent.metadata?.run_status as string) ?? "";
  if (runStatus === "failed" || runStatus === "aborted") return "dot-red";
  if (agent.status === "executing") {
    return isStalled(agent) ? "dot-yellow" : "dot-green";
  }
  return "dot-gray";
}

function agentDotTitle(agent: Agent): string {
  const runStatus = (agent.metadata?.run_status as string) ?? agent.status;
  if (runStatus === "failed") return "Failed";
  if (runStatus === "aborted") return "Aborted";
  if (agent.status === "executing") {
    return isStalled(agent) ? "Stalled (>5 min idle)" : "Running";
  }
  return "Completed";
}

function agentStatusLabel(agent: Agent): string {
  const runStatus = (agent.metadata?.run_status as string) ?? "";
  if (runStatus === "failed") return "FAILED";
  if (runStatus === "aborted") return "ABORTED";
  if (agent.status === "executing") {
    return isStalled(agent) ? "STALLED" : "RUNNING";
  }
  return "DONE";
}

function agentTagClass(agent: Agent): string {
  const runStatus = (agent.metadata?.run_status as string) ?? "";
  if (runStatus === "failed" || runStatus === "aborted") return "tag-failed";
  if (agent.status === "executing") {
    return isStalled(agent) ? "tag-stalled" : "tag-running";
  }
  return "tag-done";
}
</script>

<style scoped>
.active-agents-panel {
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

.status-badge {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.08em;
  border-radius: 4px;
  padding: 2px 8px;
}

.badge-online {
  color: #10b981;
  background: rgba(16, 185, 129, 0.12);
  border: 1px solid rgba(16, 185, 129, 0.3);
}

.badge-offline {
  color: #6b7280;
  background: rgba(107, 114, 128, 0.08);
  border: 1px solid rgba(107, 114, 128, 0.2);
}

.badge-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}

.dot-green {
  background: #10b981;
  box-shadow: 0 0 6px #10b98188;
  animation: onlinePulse 2s ease-in-out infinite;
}

.dot-gray {
  background: #4b5563;
}

@keyframes onlinePulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

/* ── Body ── */
.agent-list-body {
  flex: 1;
  overflow-y: auto;
  padding: 8px 0;
}

.agent-list-body::-webkit-scrollbar {
  width: 4px;
}
.agent-list-body::-webkit-scrollbar-track {
  background: transparent;
}
.agent-list-body::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.1);
  border-radius: 2px;
}

/* ── Empty state ── */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 32px 16px;
  gap: 8px;
}

.empty-icon {
  font-size: 24px;
  color: #374151;
}

.empty-text {
  font-size: 11px;
  color: #6b7280;
}

/* ── Agent row ── */
.agent-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 14px;
  transition: background 0.15s;
  border-bottom: 1px solid rgba(255, 255, 255, 0.04);
  cursor: pointer;
}

.agent-row:last-child {
  border-bottom: none;
}

.agent-row:hover {
  background: rgba(255, 255, 255, 0.03);
}

.agent-row.selected {
  background: rgba(59, 130, 246, 0.06);
  border-left: 2px solid #3b82f6;
}

.agent-row.stalled {
  background: rgba(245, 158, 11, 0.04);
}

/* ── Status dot ── */
.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.dot-green {
  background: #10b981;
  box-shadow: 0 0 8px #10b98166;
  animation: runningPulse 1.5s ease-in-out infinite;
}

.dot-yellow {
  background: #f59e0b;
  box-shadow: 0 0 8px #f59e0b66;
}

.dot-gray {
  background: #374151;
}

.dot-red {
  background: #ef4444;
  box-shadow: 0 0 6px #ef444466;
}

@keyframes runningPulse {
  0%, 100% { box-shadow: 0 0 4px #10b98144; }
  50% { box-shadow: 0 0 10px #10b98199; }
}

/* ── Agent info ── */
.agent-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.agent-name-row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.agent-name {
  font-size: 12px;
  font-weight: 700;
  color: #e5e7eb;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.cleo-badge {
  flex-shrink: 0;
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.05em;
  padding: 1px 5px;
  border-radius: 3px;
  background: rgba(168, 85, 247, 0.15);
  color: #c084fc;
  border: 1px solid rgba(168, 85, 247, 0.3);
}

.agent-activity {
  font-size: 10px;
  color: #9ca3af;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ── Status tag ── */
.agent-status-tag {
  flex-shrink: 0;
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.06em;
  padding: 2px 6px;
  border-radius: 3px;
}

.tag-running {
  background: rgba(16, 185, 129, 0.15);
  color: #10b981;
  border: 1px solid rgba(16, 185, 129, 0.3);
}

.tag-stalled {
  background: rgba(245, 158, 11, 0.15);
  color: #f59e0b;
  border: 1px solid rgba(245, 158, 11, 0.3);
}

.tag-failed {
  background: rgba(239, 68, 68, 0.15);
  color: #f87171;
  border: 1px solid rgba(239, 68, 68, 0.3);
}

.tag-done {
  background: rgba(107, 114, 128, 0.15);
  color: #9ca3af;
  border: 1px solid rgba(107, 114, 128, 0.3);
}
</style>
