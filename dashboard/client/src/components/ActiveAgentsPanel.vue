<template>
  <div class="active-agents-panel">
    <!-- T133: Use hierarchy-aware AgentList component -->
    <AgentList
      :agents="displayAgents"
      :selected-agent-id="store.selectedAgentId"
      @select-agent="store.selectAgent"
      @collapse-change="onCollapseChange"
    />
  </div>
</template>

<script setup lang="ts">
/**
 * ActiveAgentsPanel — shows ALL known agents with Lead/Worker hierarchy.
 * T133: Now uses AgentList.vue (extended by T129) to display orchestrator at top
 * and workers below with cost roll-up.
 *
 * Never empty: shows the most recent runs (up to 10) even when nothing
 * is currently executing.
 *
 * @task T133
 * @epic T121
 */
import { computed } from "vue";
import { useOrchestratorStore } from "../stores/orchestratorStore";
import AgentList from "./AgentList.vue";
import type { Agent } from "../types";

const store = useOrchestratorStore();

const MAX_DISPLAY = 10;

/**
 * Show up to MAX_DISPLAY most-recent agents.
 * Passed to AgentList which displays them with hierarchy awareness.
 */
const displayAgents = computed(() =>
  store.recentAgents.slice(0, MAX_DISPLAY)
);

/**
 * Handle collapse/expand state change from AgentList
 */
function onCollapseChange(isCollapsed: boolean): void {
  // Optional: store collapse state in localStorage if needed
  // For now, we just let AgentList handle its own collapse state
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
}
</style>
