<template>
  <div class="agents-page">
    <!-- ── Page Header ─────────────────────────────────────────── -->
    <div class="agents-page__header">
      <div class="agents-page__title-row">
        <h2 class="agents-page__title">&#x1F9E0; AGENT FLEET</h2>
        <div class="agents-page__stats">
          <span class="stat-chip stat-chip--active">
            <span class="stat-dot stat-dot--active"></span>
            {{ activeCount }} Active
          </span>
          <span class="stat-chip">
            {{ idleCount }} Idle
          </span>
          <span class="stat-chip stat-chip--cost">
            ${{ totalCost.toFixed(3) }} Total
          </span>
        </div>
      </div>

      <!-- Show completed toggle -->
      <div class="agents-page__controls">
        <button
          class="toggle-btn"
          :class="{ 'toggle-btn--active': showCompleted }"
          @click="showCompleted = !showCompleted"
        >
          <span class="toggle-icon">{{ showCompleted ? '&#x1F441;' : '&#x1F4A4;' }}</span>
          {{ showCompleted ? 'Hide Completed' : 'Show Completed' }}
          <span v-if="!showCompleted && completedCount > 0" class="toggle-count">{{ completedCount }}</span>
        </button>
      </div>
    </div>

    <!-- ── Content ─────────────────────────────────────────────── -->
    <div class="agents-page__content">
      <!-- Orchestrator Card (always visible) -->
      <section class="agents-section">
        <div class="section-label">ORCHESTRATOR</div>

        <div
          v-if="store.orchestratorAgent"
          class="orchestrator-card-full"
          :class="{ 'orc-active': store.orchestratorAgent.status === 'executing' }"
        >
          <div class="orc-left">
            <div class="orc-icon-wrap">
              <span class="orc-icon">&#x1F9E0;</span>
              <span class="orc-status-dot" :class="`dot-${store.orchestratorAgent.status || 'idle'}`"></span>
            </div>
            <div class="orc-info">
              <div class="orc-name">{{ store.orchestratorAgent.id }}</div>
              <div class="orc-session" v-if="store.orchestratorAgent.session_id">
                Session: {{ store.orchestratorAgent.session_id.slice(0, 16) }}...
              </div>
              <div class="orc-session" v-else>No active session</div>
            </div>
          </div>

          <div class="orc-center">
            <!-- Context window bar -->
            <div class="context-block">
              <div class="context-header-row">
                <span class="context-label">Context Window</span>
                <span class="context-values">{{ formatTokens(orchTokens) }} / 200k</span>
              </div>
              <div class="context-bar-bg">
                <div
                  class="context-bar-fill orc-fill"
                  :style="{ width: orchContextPct + '%' }"
                ></div>
              </div>
            </div>
          </div>

          <div class="orc-right">
            <div class="orc-cost-block">
              <div class="cost-row">
                <span class="cost-label">Orchestrator</span>
                <span class="cost-val">${{ store.orchestratorAgent.total_cost.toFixed(3) }}</span>
              </div>
              <div class="cost-row">
                <span class="cost-label">Workers</span>
                <span class="cost-val">${{ workersCost.toFixed(3) }}</span>
              </div>
              <div class="cost-row cost-row--total">
                <span class="cost-label">Total</span>
                <span class="cost-val">${{ totalCost.toFixed(3) }}</span>
              </div>
            </div>
          </div>

          <div class="orc-status-badge" :class="`badge-${store.orchestratorAgent.status || 'idle'}`">
            {{ (store.orchestratorAgent.status || 'idle').toUpperCase() }}
          </div>
        </div>

        <div v-else class="orchestrator-placeholder">
          <span class="orc-icon">&#x1F9E0;</span>
          <span>No orchestrator agent running</span>
        </div>
      </section>

      <!-- Worker Agents -->
      <section class="agents-section">
        <div class="section-label">
          WORKERS
          <span class="section-count">{{ visibleWorkers.length }}</span>
        </div>

        <div v-if="visibleWorkers.length === 0" class="no-agents">
          <span v-if="!showCompleted && completedCount > 0">
            All workers completed. Click "Show Completed" to view them.
          </span>
          <span v-else>No worker agents found.</span>
        </div>

        <div class="workers-grid">
          <div
            v-for="agent in visibleWorkers"
            :key="agent.id"
            class="worker-card"
            :class="{
              'worker-card--active': agent.status === 'executing',
              'worker-card--complete': agent.status === 'complete',
              'worker-card--selected': agent.id === store.selectedAgentId
            }"
            @click="store.selectAgent(agent.id)"
          >
            <div class="worker-card__header">
              <div class="worker-name-row">
                <span class="worker-name">{{ agent.name }}</span>
                <span v-if="agent.adw_step" class="worker-step">{{ agent.adw_step }}</span>
              </div>
              <span class="worker-status-badge" :class="`badge-${agent.status || 'idle'}`">
                {{ (agent.status || 'idle').toUpperCase() }}
              </span>
            </div>

            <div class="worker-task">{{ getTaskDesc(agent) }}</div>

            <!-- Context bar -->
            <div class="context-block">
              <div class="context-header-row">
                <span class="context-label">Context</span>
                <span class="context-values">{{ formatTokens(agent.input_tokens + agent.output_tokens) }} / 200k</span>
              </div>
              <div class="context-bar-bg">
                <div
                  class="context-bar-fill"
                  :style="{ width: Math.min(((agent.input_tokens + agent.output_tokens) / 200000) * 100, 100) + '%' }"
                ></div>
              </div>
            </div>

            <div class="worker-footer">
              <span class="worker-model">{{ formatModel(agent.model) }}</span>
              <span class="worker-cost">${{ agent.total_cost.toFixed(3) }}</span>
            </div>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * AgentsPage — full-page dedicated agent view.
 *
 * Shows:
 * - Large orchestrator card at top (brain icon, session info, cost, context)
 * - Worker agents as cards in a responsive grid
 * - Completed agents hidden by default with "Show Completed" toggle
 * - Only Idle + Active agents visible by default
 *
 * @task T142
 * @epic T138
 */

import { ref, computed } from "vue";
import { useOrchestratorStore } from "../stores/orchestratorStore";
import type { Agent } from "../types";

const store = useOrchestratorStore();

const showCompleted = ref(false);

// ── Computed ────────────────────────────────────────────────────

const activeCount = computed(() =>
  store.agents.filter((a) => a.status === "executing").length,
);

const idleCount = computed(() =>
  store.agents.filter((a) => a.status === "idle" || a.status === "waiting").length,
);

const completedCount = computed(() =>
  store.agents.filter((a) => a.status === "complete").length,
);

const visibleWorkers = computed(() => {
  if (showCompleted.value) return store.agents;
  return store.agents.filter((a) => a.status !== "complete");
});

const orchTokens = computed(() =>
  (store.orchestratorAgent.input_tokens || 0) + (store.orchestratorAgent.output_tokens || 0),
);

const orchContextPct = computed(() =>
  Math.min((orchTokens.value / 200000) * 100, 100),
);

const workersCost = computed(() =>
  store.agents.reduce((sum, a) => sum + (a.total_cost || 0), 0),
);

const totalCost = computed(() =>
  (store.orchestratorAgent.total_cost || 0) + workersCost.value,
);

// ── Helpers ─────────────────────────────────────────────────────

function formatTokens(n: number): string {
  if (n >= 1000) return Math.round(n / 1000) + "k";
  return String(n);
}

function formatModel(model: string): string {
  if (model.includes("sonnet")) return "sonnet";
  if (model.includes("opus")) return "opus";
  if (model.includes("haiku")) return "haiku";
  return model.slice(0, 12);
}

function getTaskDesc(agent: Agent): string {
  if (agent.adw_step) return `STEP: ${agent.adw_step}`;
  if (agent.task) return agent.task;
  if (agent.latest_summary) return agent.latest_summary;
  switch (agent.status) {
    case "executing": return "Processing task...";
    case "complete": return "Task completed";
    default: return "Ready";
  }
}
</script>

<style scoped>
.agents-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: #0a0c17;
  overflow: hidden;
}

/* ── Page Header ─────────────────────────────────────────── */
.agents-page__header {
  padding: 16px 20px 12px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.07);
  background: rgba(0, 0, 0, 0.3);
  flex-shrink: 0;
}

.agents-page__title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.agents-page__title {
  margin: 0;
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 0.12em;
  color: #e5e7eb;
  font-family: ui-monospace, monospace;
}

.agents-page__stats {
  display: flex;
  gap: 8px;
  align-items: center;
}

.stat-chip {
  font-size: 11px;
  font-weight: 600;
  padding: 3px 10px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.06);
  color: #9ca3af;
  border: 1px solid rgba(255, 255, 255, 0.08);
  display: flex;
  align-items: center;
  gap: 5px;
}

.stat-chip--active {
  background: rgba(16, 185, 129, 0.1);
  color: #10b981;
  border-color: rgba(16, 185, 129, 0.3);
}

.stat-chip--cost {
  background: rgba(139, 92, 246, 0.1);
  color: #a78bfa;
  border-color: rgba(139, 92, 246, 0.3);
}

.stat-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}

.stat-dot--active {
  animation: pulse-dot 1.5s ease-in-out infinite;
}

@keyframes pulse-dot {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0.4; }
}

/* ── Controls ──────────────────────────────────────────────── */
.agents-page__controls {
  display: flex;
  gap: 8px;
}

.toggle-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 12px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 6px;
  color: rgba(255, 255, 255, 0.5);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
}

.toggle-btn:hover {
  background: rgba(255, 255, 255, 0.08);
  color: rgba(255, 255, 255, 0.8);
}

.toggle-btn--active {
  background: rgba(0, 255, 204, 0.08);
  border-color: rgba(0, 255, 204, 0.3);
  color: #00ffcc;
}

.toggle-icon {
  font-size: 13px;
  line-height: 1;
}

.toggle-count {
  font-size: 10px;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: 8px;
  background: rgba(107, 114, 128, 0.3);
  color: #9ca3af;
}

/* ── Content ───────────────────────────────────────────────── */
.agents-page__content {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.agents-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.section-label {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.15em;
  color: #4b5563;
  text-transform: uppercase;
  padding: 4px 0;
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
  display: flex;
  align-items: center;
  gap: 8px;
}

.section-count {
  font-size: 10px;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.08);
  color: #9ca3af;
}

/* ── Orchestrator Full Card ─────────────────────────────────── */
.orchestrator-card-full {
  display: flex;
  align-items: center;
  gap: 20px;
  padding: 16px 20px;
  background: linear-gradient(135deg, rgba(139, 92, 246, 0.08) 0%, rgba(59, 130, 246, 0.04) 100%);
  border: 1.5px solid rgba(139, 92, 246, 0.3);
  border-radius: 10px;
  cursor: pointer;
  transition: all 0.2s ease;
  position: relative;
}

.orchestrator-card-full:hover {
  border-color: rgba(139, 92, 246, 0.5);
  background: linear-gradient(135deg, rgba(139, 92, 246, 0.12) 0%, rgba(59, 130, 246, 0.08) 100%);
}

.orc-active {
  border-color: rgba(16, 185, 129, 0.4);
  animation: orc-active-pulse 2s ease-in-out infinite;
}

@keyframes orc-active-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }
  50%       { box-shadow: 0 0 12px 2px rgba(16, 185, 129, 0.1); }
}

.orc-left {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
  min-width: 200px;
}

.orc-icon-wrap {
  position: relative;
  flex-shrink: 0;
}

.orc-icon {
  font-size: 28px;
  line-height: 1;
  display: block;
}

.orc-status-dot {
  position: absolute;
  bottom: -2px;
  right: -2px;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  border: 2px solid #0a0c17;
}

.dot-executing { background: #10b981; animation: pulse-dot 1.5s ease-in-out infinite; }
.dot-idle      { background: #3b82f6; }
.dot-waiting   { background: #f59e0b; }
.dot-blocked   { background: #ef4444; }
.dot-complete  { background: #6b7280; }

.orc-info {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.orc-name {
  font-size: 13px;
  font-weight: 700;
  color: #e5e7eb;
  font-family: ui-monospace, monospace;
}

.orc-session {
  font-size: 11px;
  color: #6b7280;
  font-family: ui-monospace, monospace;
}

.orc-center {
  flex: 1;
  min-width: 0;
}

.orc-right {
  flex-shrink: 0;
  min-width: 160px;
}

.orc-status-badge {
  position: absolute;
  top: 12px;
  right: 16px;
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.08em;
  padding: 3px 10px;
  border-radius: 4px;
}

.badge-idle     { background: rgba(59,130,246,0.2); color: #3b82f6; border: 1px solid rgba(59,130,246,0.4); }
.badge-executing{ background: rgba(16,185,129,0.2); color: #10b981; border: 1px solid rgba(16,185,129,0.4); }
.badge-waiting  { background: rgba(245,158,11,0.2); color: #f59e0b; border: 1px solid rgba(245,158,11,0.4); }
.badge-blocked  { background: rgba(239,68,68,0.2);  color: #ef4444; border: 1px solid rgba(239,68,68,0.4); }
.badge-complete { background: rgba(107,114,128,0.2);color: #6b7280; border: 1px solid rgba(107,114,128,0.4); }

/* ── Context block (shared) ──────────────────────────────────── */
.context-block {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.context-header-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.context-label {
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.06em;
  color: #4b5563;
  text-transform: uppercase;
}

.context-values {
  font-size: 11px;
  font-weight: 600;
  color: #9ca3af;
  font-family: ui-monospace, monospace;
}

.context-bar-bg {
  height: 6px;
  background: rgba(255, 255, 255, 0.06);
  border-radius: 3px;
  overflow: hidden;
}

.context-bar-fill {
  height: 100%;
  background: linear-gradient(90deg, #06b6d4 0%, #0891b2 100%);
  border-radius: 3px;
  transition: width 0.3s ease;
}

.orc-fill {
  background: linear-gradient(90deg, #a78bfa 0%, #8b5cf6 100%);
}

/* ── Cost block ──────────────────────────────────────────────── */
.orc-cost-block {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 12px;
  background: rgba(0, 0, 0, 0.2);
  border-radius: 6px;
}

.cost-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 11px;
}

.cost-row--total {
  margin-top: 3px;
  padding-top: 4px;
  border-top: 1px solid rgba(139, 92, 246, 0.15);
  font-weight: 600;
}

.cost-label {
  color: #6b7280;
}

.cost-val {
  color: #e5e7eb;
  font-family: ui-monospace, monospace;
}

/* ── Orchestrator placeholder ────────────────────────────────── */
.orchestrator-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 24px;
  background: rgba(0, 0, 0, 0.2);
  border: 1px dashed rgba(139, 92, 246, 0.25);
  border-radius: 10px;
  color: #4b5563;
  font-size: 13px;
}

/* ── Workers Grid ────────────────────────────────────────────── */
.workers-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
}

.worker-card {
  background: rgba(255, 255, 255, 0.02);
  border: 1px solid rgba(255, 255, 255, 0.07);
  border-radius: 8px;
  padding: 12px;
  cursor: pointer;
  transition: all 0.15s ease;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.worker-card:hover {
  background: rgba(255, 255, 255, 0.04);
  border-color: rgba(255, 255, 255, 0.12);
}

.worker-card--active {
  border-color: rgba(16, 185, 129, 0.3);
  background: rgba(16, 185, 129, 0.03);
}

.worker-card--complete {
  opacity: 0.6;
}

.worker-card--selected {
  border-color: rgba(139, 92, 246, 0.5);
  box-shadow: 0 0 0 1px rgba(139, 92, 246, 0.3);
}

.worker-card__header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 8px;
}

.worker-name-row {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.worker-name {
  font-size: 12px;
  font-weight: 700;
  color: #e5e7eb;
  font-family: ui-monospace, monospace;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.worker-step {
  font-size: 10px;
  color: #06b6d4;
  font-family: ui-monospace, monospace;
}

.worker-task {
  font-size: 11px;
  color: #6b7280;
  line-height: 1.4;
  min-height: 1.4em;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.worker-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 11px;
}

.worker-model {
  padding: 2px 6px;
  border-radius: 4px;
  background: rgba(107, 114, 128, 0.15);
  color: #9ca3af;
  font-family: ui-monospace, monospace;
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
}

.worker-cost {
  color: #9ca3af;
  font-family: ui-monospace, monospace;
  font-weight: 600;
}

/* ── No agents placeholder ───────────────────────────────────── */
.no-agents {
  padding: 24px;
  text-align: center;
  color: #4b5563;
  font-size: 12px;
  background: rgba(0, 0, 0, 0.1);
  border-radius: 8px;
  border: 1px dashed rgba(255, 255, 255, 0.05);
}

/* ── Scrollbar ───────────────────────────────────────────────── */
.agents-page__content::-webkit-scrollbar { width: 4px; }
.agents-page__content::-webkit-scrollbar-track { background: transparent; }
.agents-page__content::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.08); border-radius: 2px; }
</style>
