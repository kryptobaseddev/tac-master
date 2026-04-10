<script setup lang="ts">
/**
 * CostDashboard — live session cost metrics panel.
 *
 * Shows real-time cost tracking for the active orchestrator session:
 *   • Session Cost     — total cost for current session ($X.XX)
 *   • Input Tokens    — tokens consumed as input (formatted: 12.4k)
 *   • Output Tokens   — tokens generated as output (formatted: 12.4k)
 *   • Burn Rate       — USD per hour ($X.XX/hr)
 *   • Total Run Cost  — sum of all agent run costs
 *   • Active Runs     — count of currently executing agents
 *
 * Data is live from store.costMetrics (computed from orchestratorAgent).
 * The store is kept in sync by WebSocket heartbeats.
 *
 * Collapsible: header row 'COST DASHBOARD' with toggle.
 * Color coding:   green (<$1), yellow ($1-5), red (>$5)
 * Theme: dark (zinc/slate with cyan accents)
 *
 * @task T127
 * @epic T121
 */

import { ref, computed } from "vue";
import { useOrchestratorStore } from "../stores/orchestratorStore";

const store = useOrchestratorStore();
const isCollapsed = ref(false);

// Computed cost values
const metrics = computed(() => store.costMetrics);
const totalAgentCost = computed(() =>
  store.agents.reduce((sum, agent) => sum + (agent.total_cost || 0), 0)
);
const activeRunCount = computed(() => store.runningAgents.length);

// Has active session
const hasActiveSession = computed(() => {
  const cost = metrics.value?.sessionCost ?? 0;
  const tokens = (metrics.value?.inputTokens ?? 0) + (metrics.value?.outputTokens ?? 0);
  return cost > 0 || tokens > 0;
});

// Formatters
function formatCost(value: number): string {
  if (!isFinite(value) || value < 0) return "$0.00";
  return `$${value.toFixed(2)}`;
}

function formatTokens(value: number): string {
  if (!isFinite(value) || value < 0) return "0";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return String(Math.round(value));
}

// Color coding for cost
function getCostColor(cost: number): string {
  if (cost < 1) return "text-green-400"; // green
  if (cost < 5) return "text-yellow-400"; // yellow
  return "text-red-400"; // red
}

function toggleCollapse() {
  isCollapsed.value = !isCollapsed.value;
}
</script>

<template>
  <div class="cost-dashboard">
    <!-- Header with collapse toggle -->
    <div class="cost-header">
      <h3 class="cost-title">COST DASHBOARD</h3>
      <button
        class="cost-collapse-btn"
        @click="toggleCollapse"
        :title="isCollapsed ? 'Expand' : 'Collapse'"
      >
        <span class="cost-collapse-icon">{{ isCollapsed ? "›" : "‹" }}</span>
      </button>
    </div>

    <!-- Expanded view (metrics tiles) -->
    <div v-if="!isCollapsed" class="cost-metrics-grid">
      <!-- No active session fallback -->
      <div v-if="!hasActiveSession" class="cost-no-session">
        <span class="cost-no-session-text">No active session</span>
      </div>

      <!-- KPI Tiles -->
      <template v-else>
        <!-- Session Cost -->
        <div class="cost-tile">
          <div class="cost-tile-label">Session Cost</div>
          <div :class="['cost-tile-value', getCostColor(metrics.sessionCost)]">
            {{ formatCost(metrics.sessionCost) }}
          </div>
        </div>

        <!-- Input Tokens -->
        <div class="cost-tile">
          <div class="cost-tile-label">Input Tokens</div>
          <div class="cost-tile-value">{{ formatTokens(metrics.inputTokens) }}</div>
        </div>

        <!-- Output Tokens -->
        <div class="cost-tile">
          <div class="cost-tile-label">Output Tokens</div>
          <div class="cost-tile-value">{{ formatTokens(metrics.outputTokens) }}</div>
        </div>

        <!-- Burn Rate -->
        <div class="cost-tile">
          <div class="cost-tile-label">Burn Rate</div>
          <div :class="['cost-tile-value', getCostColor(metrics.burnRatePerHour)]">
            {{ formatCost(metrics.burnRatePerHour) }}/hr
          </div>
        </div>

        <!-- Total Run Cost -->
        <div class="cost-tile">
          <div class="cost-tile-label">Total Run Cost</div>
          <div :class="['cost-tile-value', getCostColor(totalAgentCost)]">
            {{ formatCost(totalAgentCost) }}
          </div>
        </div>

        <!-- Active Runs -->
        <div class="cost-tile">
          <div class="cost-tile-label">Active Runs</div>
          <div class="cost-tile-value">{{ activeRunCount }}</div>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.cost-dashboard {
  background: linear-gradient(135deg, rgba(15, 23, 42, 0.9), rgba(30, 41, 59, 0.85));
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 8px;
  overflow: hidden;
  font-family: var(--cc-font, ui-monospace, monospace);
}

/* ── Header ──────────────────────────────────────────────────────── */
.cost-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  background: rgba(15, 23, 42, 0.6);
  border-bottom: 1px solid rgba(148, 163, 184, 0.15);
}

.cost-title {
  margin: 0;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: #94a3b8; /* slate-400 */
}

.cost-collapse-btn {
  background: none;
  border: none;
  padding: 4px 8px;
  cursor: pointer;
  color: #64748b; /* slate-500 */
  transition: color 150ms ease;
  display: flex;
  align-items: center;
  justify-content: center;
}

.cost-collapse-btn:hover {
  color: #94a3b8; /* slate-400 */
}

.cost-collapse-icon {
  font-size: 14px;
  font-weight: 700;
}

/* ── Metrics Grid ────────────────────────────────────────────────── */
.cost-metrics-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 12px;
  padding: 12px;
}

/* ── No Session State ────────────────────────────────────────────── */
.cost-no-session {
  grid-column: 1 / -1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px 16px;
  color: #64748b; /* slate-500 */
}

.cost-no-session-text {
  font-size: 12px;
  letter-spacing: 0.05em;
}

/* ── KPI Tile ────────────────────────────────────────────────────── */
.cost-tile {
  background: rgba(51, 65, 85, 0.4); /* slate-700 with opacity */
  border: 1px solid rgba(148, 163, 184, 0.15);
  border-radius: 6px;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  transition: all 150ms ease;
}

.cost-tile:hover {
  background: rgba(51, 65, 85, 0.6);
  border-color: rgba(148, 163, 184, 0.3);
}

.cost-tile-label {
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #64748b; /* slate-500 */
}

.cost-tile-value {
  font-size: 16px;
  font-weight: 700;
  color: #e2e8f0; /* slate-200 */
  letter-spacing: -0.01em;
  word-break: break-word;
}

/* ── Color variants for cost values ──────────────────────────────── */
.text-green-400 {
  color: #4ade80; /* green-400 */
}

.text-yellow-400 {
  color: #facc15; /* yellow-400 */
}

.text-red-400 {
  color: #f87171; /* red-400 */
}

/* ── Responsive: collapse to 2-column on smaller screens ──────────– */
@media (max-width: 768px) {
  .cost-metrics-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media (max-width: 480px) {
  .cost-metrics-grid {
    grid-template-columns: 1fr;
  }

  .cost-tile-value {
    font-size: 14px;
  }
}
</style>
