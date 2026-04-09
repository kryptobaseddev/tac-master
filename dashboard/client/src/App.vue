<script setup lang="ts">
import { ref, onMounted, computed } from "vue";
import { useOrchestratorStore } from "./stores/orchestratorStore";
import AgentList from "./components/AgentList.vue";
import EventStream from "./components/EventStream.vue";
import RunDetailsPanel from "./components/RunDetailsPanel.vue";
import LiveExecutionPanel from "./components/LiveExecutionPanel.vue";
import RepoBoard from "./components/RepoBoard.vue";
import ConfigPage from "./components/ConfigPage.vue";

const store = useOrchestratorStore();

type Tab = "dashboard" | "repos" | "config";
const activeTab = ref<Tab>("dashboard");

const isConnected = computed(() => store.isConnected);
const liveRuns = computed(() => store.runningAgents.length);
const totalRepos = computed(() => store.repos.length);
const totalTokens = computed(() => store.totalTokensToday);
const totalCost = computed(() => store.stats.cost);

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

onMounted(() => {
  store.initialize();
});

function handleRunSelected(_id: string) {
  activeTab.value = "dashboard";
}
</script>

<template>
  <div class="app">
    <!-- Global header bar with tab nav + stats -->
    <header class="app-header">
      <div class="app-header-left">
        <div class="app-brand">
          <span class="app-brand-accent">tac-</span>master
        </div>
        <span
          class="app-connection-dot"
          :class="{ 'is-connected': isConnected }"
          :title="isConnected ? 'WebSocket connected' : 'Reconnecting…'"
        />
        <nav class="app-tabs">
          <button
            :class="['app-tab', { active: activeTab === 'dashboard' }]"
            @click="activeTab = 'dashboard'"
          >
            Dashboard
          </button>
          <button
            :class="['app-tab', { active: activeTab === 'repos' }]"
            @click="activeTab = 'repos'"
          >
            Repos
          </button>
          <button
            :class="['app-tab', { active: activeTab === 'config' }]"
            @click="activeTab = 'config'"
          >
            Config
          </button>
        </nav>
      </div>
      <div class="app-header-right">
        <div class="stat">
          <span class="stat-label">live</span>
          <span class="stat-value stat-live">{{ liveRuns }}</span>
        </div>
        <div class="stat">
          <span class="stat-label">repos</span>
          <span class="stat-value">{{ totalRepos }}</span>
        </div>
        <div class="stat">
          <span class="stat-label">runs</span>
          <span class="stat-value">{{ store.agents.length }}</span>
        </div>
        <div class="stat">
          <span class="stat-label">tokens/day</span>
          <span class="stat-value">{{ fmtTokens(totalTokens) }}</span>
        </div>
        <div class="stat">
          <span class="stat-label">cost/day</span>
          <span class="stat-value">${{ totalCost.toFixed(2) }}</span>
        </div>
      </div>
    </header>

    <!-- Main content area -->
    <main class="app-main">
      <!-- DASHBOARD TAB -->
      <template v-if="activeTab === 'dashboard'">
        <AgentList
          :agents="store.agents"
          :selected-agent-id="store.selectedAgentId"
          @select-agent="store.selectAgent"
        />
        <EventStream class="app-stream" />
        <!-- T039: LiveExecutionPanel — thinking/tool/response/hook feed -->
        <LiveExecutionPanel class="app-live-execution" />
        <RunDetailsPanel />
      </template>

      <!-- REPOS TAB -->
      <RepoBoard
        v-else-if="activeTab === 'repos'"
        class="app-repos"
        @select-run="handleRunSelected"
      />

      <!-- CONFIG TAB -->
      <ConfigPage v-else-if="activeTab === 'config'" class="app-config" />
    </main>
  </div>
</template>

<style scoped>
.app {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: var(--color-bg-primary, #0a0a0a);
  color: var(--color-text-primary, #e4e7eb);
  font-family: var(--font-mono, ui-monospace, monospace);
  overflow: hidden;
}
.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 20px;
  border-bottom: 1px solid var(--color-border, #2a2a2a);
  background: #0f0f12;
  flex-shrink: 0;
  gap: 16px;
}
.app-header-left {
  display: flex;
  align-items: center;
  gap: 14px;
}
.app-brand {
  font-size: 16px;
  font-weight: 700;
  color: #f5f7fa;
  letter-spacing: 0.02em;
}
.app-brand-accent {
  color: #a855f7;
}
.app-connection-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #ef4444;
  transition: background 0.3s;
}
.app-connection-dot.is-connected {
  background: #10b981;
  box-shadow: 0 0 8px #10b98166;
}
.app-tabs {
  display: flex;
  gap: 2px;
  margin-left: 12px;
}
.app-tab {
  background: transparent;
  border: 1px solid transparent;
  color: #6b7280;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  padding: 6px 12px;
  border-radius: 4px;
  cursor: pointer;
  font-family: inherit;
  transition: all 0.15s;
}
.app-tab:hover {
  color: #e4e7eb;
  background: #1a1a1a;
}
.app-tab.active {
  color: #f5f7fa;
  background: #2a2a2a;
  border-bottom: 2px solid #a855f7;
}
.app-header-right {
  display: flex;
  gap: 22px;
  align-items: center;
}
.stat {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
}
.stat-label {
  font-size: 9px;
  color: #6b7280;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}
.stat-value {
  font-size: 14px;
  font-weight: 700;
  color: #f5f7fa;
}
.stat-live {
  color: #3b82f6;
}
.app-main {
  flex: 1;
  display: flex;
  overflow: hidden;
  min-height: 0;
}
.app-stream {
  flex: 1;
  min-width: 0;
}
.app-live-execution {
  width: 340px;
  min-width: 280px;
  flex-shrink: 0;
}
.app-repos {
  flex: 1;
  overflow-y: auto;
}
.app-config {
  flex: 1;
  overflow-y: auto;
}

/* Mobile friendliness — stack panels on narrow screens */
@media (max-width: 900px) {
  .app-header {
    flex-wrap: wrap;
    gap: 10px;
  }
  .app-header-right {
    gap: 12px;
  }
  .stat-label {
    display: none;
  }
  .app-main {
    flex-direction: column;
    overflow-y: auto;
  }
}
</style>
