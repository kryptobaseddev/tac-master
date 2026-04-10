<script setup lang="ts">
import { ref, onMounted } from "vue";
import { useOrchestratorStore } from "./stores/orchestratorStore";
import { useChatStore } from "./stores/chatStore";
// T071 / T092: WebSocket composable — connects on mount, routes all WsMessage
// types to the store, and handles exponential-backoff reconnect automatically.
import { useWebSocket } from "./composables/useWebSocket";

// Layout shell
import CommandCenterLayout from "./components/CommandCenterLayout.vue";

// Header + sidebar + status
import HeaderBar from "./components/HeaderBar.vue";
import RepoSidebar from "./components/RepoSidebar.vue";
import StatusBar from "./components/StatusBar.vue";

// Main panels (center column)
import ActiveAgentsPanel from "./components/ActiveAgentsPanel.vue";
import PipelineFlow from "./components/PipelineFlow.vue";
import CostDashboard from "./components/CostDashboard.vue";
import IssueDetails from "./components/IssueDetails.vue";
import DependencyGraph from "./components/DependencyGraph.vue";

// Right panel
import LiveExecutionPanel from "./components/LiveExecutionPanel.vue";
import OrchestratorChat from "./components/OrchestratorChat.vue";

// Sidebar — CLEO task tree
import EpicTaskTree from "./components/command-center/EpicTaskTree.vue";

// Operator Log panel
import OperatorLog from "./components/OperatorLog.vue";

// Phase detail modal (T055)
import PhaseDetailModal from "./components/PhaseDetailModal.vue";

// System Logs viewer
import SystemLogs from "./components/SystemLogs.vue";

// T053: Operator command bar + toast notifications
import CommandBar from "./components/CommandBar.vue";
import Toast from "./components/Toast.vue";

// T128: Cmd+K command palette overlay
import CommandPalette from "./components/CommandPalette.vue";

// Legacy pages (repos, config)
import RepoBoard from "./components/RepoBoard.vue";
import ConfigPage from "./components/ConfigPage.vue";

// ── Store ─────────────────────────────────────────────────────────
const store = useOrchestratorStore();
const chatStore = useChatStore();

// ── WebSocket (T071/T092) ─────────────────────────────────────────
// useWebSocket connects on mount, routes all WsMessage types to the store,
// and automatically reconnects with exponential backoff. The composable
// also fetches GET /events/recent after each successful reconnect.
// Store state (isConnected, reconnectCount) is exposed to the template
// via the store itself for the StatusBar; the composable keeps its own
// reactive refs as the authoritative source for connection health.
const { isConnected: wsConnected, connectionStatus, reconnectCount } = useWebSocket();

// ── Phase detail modal state (T055) ──────────────────────────────
const phaseModalVisible = ref(false);
const phaseModalAdwId = ref<string | null>(null);
const phaseModalPhase = ref<string | null>(null);

function openPhaseModal(payload: { adwId: string; phase: string }): void {
  phaseModalAdwId.value = payload.adwId;
  phaseModalPhase.value = payload.phase;
  phaseModalVisible.value = true;
}
function closePhaseModal(): void {
  phaseModalVisible.value = false;
}

// ── Tab routing ───────────────────────────────────────────────────
type Tab = "dashboard" | "repos" | "config" | "logs";
const activeTab = ref<Tab>("dashboard");

// ── Right panel tabs (T104) ────────────────────────────────────
type RightPanelTab = "execution" | "chat";
const rightPanelTab = ref<RightPanelTab>("execution");

// ── Repo selection ────────────────────────────────────────────────
// currentRepoUrl: user-selected repo (shown in HeaderBar dropdown)
const currentRepoUrl = ref<string>("");

// When a repo is selected in the sidebar, find its most recent run
// and select it in the store so IssueDetails / PipelineFlow / LiveExecution
// panels all update automatically.
function handleRepoSelect(url: string) {
  currentRepoUrl.value = url;

  // Find most-recent run for this repo (running first, then by started_at)
  const repoRuns = store.agents
    .filter((a) => {
      const meta = a.metadata ?? {};
      return (meta.repo_url as string) === url;
    })
    .sort((a, b) => {
      // Running agents first
      const aRunning = a.status === "executing" ? 1 : 0;
      const bRunning = b.status === "executing" ? 1 : 0;
      if (bRunning !== aRunning) return bRunning - aRunning;
      // Then by recency — fall back to string comparison of id
      return (b.id ?? "").localeCompare(a.id ?? "");
    });

  if (repoRuns.length > 0) {
    store.selectAgent(repoRuns[0].id);
  }
}

// ── Initialize: HTTP bootstrap only (WS handled by useWebSocket above) ────
// store.loadInitial() fetches /api/runs, /api/repos, /events/recent over HTTP.
// store.initialize() would also call the old connectWebSocket() which is now
// superseded by the useWebSocket composable wired above.
onMounted(() => {
  store.loadInitial();

  // Default currentRepoUrl to first repo once data arrives
  const unsub = store.$subscribe(() => {
    if (!currentRepoUrl.value && store.repos.length > 0) {
      currentRepoUrl.value = store.repos[0].url;
      unsub();
    }
  });

  // Load chat history on mount (T104)
  const agentId = store.orchestratorAgentId || "tac-master";
  if (agentId) {
    chatStore.loadHistory(agentId).catch((err) => {
      console.warn("[App.vue] Failed to load chat history:", err);
      // Not an error — just no prior session
    });
  }
});
</script>

<template>
  <CommandCenterLayout>
    <!-- ── Header ──────────────────────────────────────────── -->
    <template #header>
      <HeaderBar
        :active-tab="activeTab"
        :current-repo-url="currentRepoUrl"
        @update:active-tab="activeTab = ($event as Tab)"
        @update:current-repo-url="currentRepoUrl = $event"
      />
    </template>

    <!-- ── Left sidebar: repo metrics + CLEO task tree ────────── -->
    <template #sidebar>
      <RepoSidebar
        :selected-repo-url="currentRepoUrl"
        :active-tab="activeTab"
        @select-repo="handleRepoSelect"
        @navigate="activeTab = ($event as Tab)"
        @open-logs="activeTab = 'logs'"
      />
      <EpicTaskTree />
      <OperatorLog />
    </template>

    <!-- ── Center main area ───────────────────────────────── -->
    <template #main>
      <!-- Dashboard view: panels -->
      <template v-if="activeTab === 'dashboard'">
        <PipelineFlow @phase-click="openPhaseModal" />
        <CostDashboard />
        <ActiveAgentsPanel />
        <div class="dep-graph-panel">
          <DependencyGraph />
        </div>
        <IssueDetails />
      </template>

      <!-- System logs page -->
      <SystemLogs
        v-else-if="activeTab === 'logs'"
        class="cc-page"
      />

      <!-- Repos page -->
      <RepoBoard
        v-else-if="activeTab === 'repos'"
        class="cc-page"
      />

      <!-- Config page -->
      <ConfigPage
        v-else-if="activeTab === 'config'"
        class="cc-page"
      />
    </template>

    <!-- ── Right sidebar: live execution feed & chat (T104) ──── -->
    <template #right>
      <!-- Right panel tabs (T104) -->
      <div class="right-panel-container">
        <div class="right-panel-tabs">
          <button
            class="right-panel-tab"
            :class="{ 'right-panel-tab--active': rightPanelTab === 'execution' }"
            @click="rightPanelTab = 'execution'"
          >
            Execution
          </button>
          <button
            class="right-panel-tab"
            :class="{ 'right-panel-tab--active': rightPanelTab === 'chat' }"
            @click="rightPanelTab = 'chat'"
          >
            Chat
          </button>
        </div>
        <div class="right-panel-content">
          <LiveExecutionPanel v-show="rightPanelTab === 'execution'" />
          <OrchestratorChat v-show="rightPanelTab === 'chat'" />
        </div>
      </div>
    </template>

    <!-- ── Command bar (T053) ────────────────────────────── -->
    <template #commandbar>
      <CommandBar />
    </template>

    <!-- ── Status bar ─────────────────────────────────────── -->
    <template #statusbar>
      <StatusBar />
    </template>
  </CommandCenterLayout>

  <!-- ── Toast overlay (T053) ──────────────────────────────── -->
  <Toast />

  <!-- ── Cmd+K command palette overlay (T128) ──────────────── -->
  <CommandPalette />

  <!-- ── Phase detail modal (T055) ─────────────────────────── -->
  <PhaseDetailModal
    :visible="phaseModalVisible"
    :adw-id="phaseModalAdwId"
    :phase="phaseModalPhase"
    @close="closePhaseModal"
  />
</template>

<style>
/* Global resets — keep body/html full-height */
html, body, #app {
  margin: 0;
  padding: 0;
  height: 100%;
  overflow: hidden;
}

/* Page content areas (repos, config) fill the main column */
.cc-page {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
}

/* Task dependency graph panel — fills remaining center space */
.dep-graph-panel {
  flex: 1;
  min-height: 300px;
  display: flex;
  flex-direction: column;
  border: 1px solid rgba(255, 255, 255, 0.07);
  border-radius: 6px;
  overflow: hidden;
  background: #0d0f1a;
}

/* Right panel container with tabs (T104) */
.right-panel-container {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.right-panel-tabs {
  display: flex;
  gap: 0;
  padding: 0 8px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.07);
  background: rgba(0, 0, 0, 0.2);
  flex-shrink: 0;
}

.right-panel-tab {
  padding: 8px 12px;
  background: transparent;
  border: none;
  border-bottom: 2px solid transparent;
  color: rgba(255, 255, 255, 0.5);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
}

.right-panel-tab:hover {
  color: rgba(255, 255, 255, 0.7);
}

.right-panel-tab--active {
  color: #00ffcc;
  border-bottom-color: #00ffcc;
}

.right-panel-content {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
</style>
