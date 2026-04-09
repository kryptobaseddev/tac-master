<template>
  <div class="active-agents-panel">
    <div class="panel-header">
      <span class="panel-title">ACTIVE AGENTS</span>
      <span class="online-badge">
        <span class="online-dot" />
        ONLINE &middot; {{ runningAgents.length }}
      </span>
    </div>

    <div class="agent-list-body">
      <div v-if="runningAgents.length === 0" class="empty-state">
        <span class="empty-icon">&#9675;</span>
        <span class="empty-text">No agents currently running</span>
      </div>

      <div
        v-for="agent in runningAgents"
        :key="agent.adw_id"
        class="agent-row"
        :class="{ stalled: isStalled(agent) }"
      >
        <span
          class="status-dot"
          :class="isStalled(agent) ? 'dot-stalled' : 'dot-running'"
          :title="isStalled(agent) ? 'Stalled (>5 min idle)' : 'Running'"
        />
        <div class="agent-info">
          <span class="agent-name">{{ formatName(agent) }}</span>
          <span class="agent-activity">{{ formatActivity(agent) }}</span>
        </div>
        <span
          class="agent-status-tag"
          :class="isStalled(agent) ? 'tag-stalled' : 'tag-running'"
        >
          {{ isStalled(agent) ? 'STALLED' : 'RUNNING' }}
        </span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * ActiveAgentsPanel — live list of currently-running tac-master ADW runs.
 *
 * Pulls from the orchestratorStore's `runningAgents` computed (already
 * driven by WebSocket run_update messages). No additional HTTP call needed;
 * the store hydrates on mount and stays live via WS.
 *
 * Stall detection: if the run's `started_at` is > 5 min ago and there are
 * no events in the last 5 min for that adw_id, mark it yellow/stalled.
 *
 * @task T038
 * @epic T036
 */
import { computed } from "vue";
import { useOrchestratorStore } from "../stores/orchestratorStore";
import type { Agent } from "../types";

const store = useOrchestratorStore();

const STALL_MS = 5 * 60 * 1000; // 5 minutes

const runningAgents = computed(() => store.runningAgents);

/**
 * Display name: role prefix + first 8 chars of adw_id.
 * Falls back to agent.name from the store if no adw_id.
 */
function formatName(agent: Agent): string {
  const id = agent.id ?? agent.adw_id;
  const shortId = id ? id.slice(0, 8) : "unknown";
  const step = agent.adw_step ?? "Worker";
  const role = step.charAt(0).toUpperCase() + step.slice(1).split("_")[0];
  return `${role}-${shortId}`;
}

/**
 * One-line activity description from the most-recent event's phase + content.
 */
function formatActivity(agent: Agent): string {
  const issueNum = agent.metadata?.issue_number ?? "?";
  const repoSlug = agent.metadata?.repo_slug ?? "";
  const phase = agent.adw_step ?? "processing";

  // Find the most recent event for this agent
  const events = store.eventStreamEntries.filter((e) => e.agentId === agent.id);
  if (events.length > 0) {
    const last = events[events.length - 1];
    const phaseName = last.metadata?.phase ?? phase;
    return `Processing Issue #${issueNum} ${repoSlug ? `(${repoSlug}) ` : ""}— ${phaseName} phase`;
  }

  return `Processing Issue #${issueNum} — ${phase} phase`;
}

/**
 * Stalled = running + no events in the last 5 minutes.
 */
function isStalled(agent: Agent): boolean {
  const events = store.eventStreamEntries.filter((e) => e.agentId === agent.id);
  if (events.length === 0) {
    // No events at all; consider stalled if started > 5 min ago
    const startedAt = agent.metadata?.started_at_unix as number | undefined;
    if (!startedAt) return false;
    return Date.now() - startedAt * 1000 > STALL_MS;
  }
  const last = events[events.length - 1];
  const lastTs = typeof last.timestamp === "string"
    ? new Date(last.timestamp).getTime()
    : Number(last.timestamp);
  return Date.now() - lastTs > STALL_MS;
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

.online-badge {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: #10b981;
  background: rgba(16, 185, 129, 0.12);
  border: 1px solid rgba(16, 185, 129, 0.3);
  border-radius: 4px;
  padding: 2px 8px;
}

.online-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #10b981;
  box-shadow: 0 0 6px #10b98188;
  animation: onlinePulse 2s ease-in-out infinite;
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
}

.agent-row:last-child {
  border-bottom: none;
}

.agent-row:hover {
  background: rgba(255, 255, 255, 0.03);
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

.dot-running {
  background: #10b981;
  box-shadow: 0 0 8px #10b98166;
  animation: runningPulse 1.5s ease-in-out infinite;
}

.dot-stalled {
  background: #f59e0b;
  box-shadow: 0 0 8px #f59e0b66;
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

.agent-name {
  font-size: 12px;
  font-weight: 700;
  color: #e5e7eb;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
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
</style>
