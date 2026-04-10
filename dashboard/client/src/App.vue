<script setup lang="ts">
import { ref, computed, watch, onMounted } from "vue";
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

// T126: ADW Swimlanes view
import AdwSwimlanes from "./components/AdwSwimlanes.vue";

// T142: Dedicated Agents page
import AgentsPage from "./components/AgentsPage.vue";

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

// ── Computed: active (running) ADW agents ─────────────────────────
const runningAgents = computed(() => store.runningAgents);

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
type Tab = "dashboard" | "agents" | "repos" | "config" | "logs";
const activeTab = ref<Tab>("dashboard");

// ── Right panel tabs (T104) ────────────────────────────────────
// Default to CHAT when no runs are live; auto-switch to execution when runs start.
type RightPanelTab = "execution" | "chat";
const rightPanelTab = ref<RightPanelTab>("chat");

// Auto-switch right panel: chat→execution when a run starts, execution→chat when idle
watch(
  () => store.stats.running,
  (count, prev) => {
    if (count > 0 && (prev ?? 0) === 0) {
      // Run just started — show execution
      rightPanelTab.value = "execution";
    } else if (count === 0 && (prev ?? 0) > 0) {
      // Run just ended — go back to chat
      rightPanelTab.value = "chat";
    }
  }
);

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

  // T126: Fetch ADWs for swimlanes view on mount
  store.fetchAdws();

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
      <!-- Dashboard view: panels or swimlanes -->
      <template v-if="activeTab === 'dashboard'">
        <!-- T130: View mode toggle (Dashboard / Swimlanes) -->
        <div class="main-header">
          <div class="view-mode-toggle">
            <button
              class="view-mode-btn"
              :class="{ 'view-mode-btn--active': store.viewMode === 'dashboard' }"
              @click="store.setViewMode('dashboard')"
            >
              Dashboard
            </button>
            <button
              class="view-mode-btn"
              :class="{ 'view-mode-btn--active': store.viewMode === 'swimlanes' }"
              @click="store.setViewMode('swimlanes')"
            >
              Swimlanes
            </button>
          </div>
        </div>

        <!-- Dashboard panels (default view) -->
        <div v-if="store.viewMode === 'dashboard'" class="dashboard-panels">
          <!-- Orchestrator + active agent summary row -->
          <div class="agent-summary-row">
            <!-- Orchestrator card -->
            <div
              class="orch-card"
              :class="{ 'orch-card--busy': store.stats.running > 0 }"
              @click="activeTab = 'agents'"
              title="View all agents"
            >
              <div class="orch-card__icon">&#129504;</div>
              <div class="orch-card__body">
                <div class="orch-card__title">TAC-MASTER</div>
                <div class="orch-card__status">
                  <span class="orch-card__dot" :class="store.stats.running > 0 ? 'dot--busy' : 'dot--idle'"></span>
                  {{ store.stats.running > 0 ? `${store.stats.running} run${store.stats.running > 1 ? 's' : ''} active` : 'Idle' }}
                </div>
              </div>
              <div class="orch-card__cost">${{ store.stats.cost.toFixed(2) }}</div>
            </div>

            <!-- Active agent mini-cards -->
            <div
              v-for="agent in runningAgents.slice(0, 4)"
              :key="agent.id"
              class="agent-mini-card"
              @click="activeTab = 'agents'"
              :title="`${agent.name} — click to view`"
            >
              <div class="agent-mini-card__name">{{ agent.name }}</div>
              <div class="agent-mini-card__phase">{{ agent.adw_step ?? agent.status }}</div>
              <div class="agent-mini-card__cost">${{ (agent.total_cost ?? 0).toFixed(3) }}</div>
            </div>

            <!-- Overflow badge -->
            <div v-if="runningAgents.length > 4" class="agent-overflow-badge">
              +{{ runningAgents.length - 4 }} more
            </div>
          </div>

          <PipelineFlow @phase-click="openPhaseModal" />
          <CostDashboard />
          <ActiveAgentsPanel />
          <div class="dep-graph-panel">
            <DependencyGraph />
          </div>
          <IssueDetails />
        </div>

        <!-- Swimlanes view (full height) -->
        <div v-else-if="store.viewMode === 'swimlanes'" class="swimlanes-container">
          <AdwSwimlanes />
        </div>
      </template>

      <!-- Agents page (T142) -->
      <AgentsPage
        v-else-if="activeTab === 'agents'"
        class="cc-page"
      />

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
            class="right-panel-tab right-panel-tab--chat"
            :class="{ 'right-panel-tab--active': rightPanelTab === 'chat' }"
            @click="rightPanelTab = 'chat'"
            title="Chat with Orchestrator AI"
          >
            <span class="chat-tab-icon">&#x1F4AC;</span>
            Chat
            <span class="chat-tab-badge" v-if="rightPanelTab !== 'chat'">AI</span>
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

/* Chat tab with AI badge */
.right-panel-tab--chat {
  display: flex;
  align-items: center;
  gap: 4px;
}

.chat-tab-icon {
  font-size: 11px;
  line-height: 1;
}

.chat-tab-badge {
  font-size: 8px;
  font-weight: 700;
  letter-spacing: 0.05em;
  padding: 1px 5px;
  border-radius: 8px;
  background: linear-gradient(135deg, #8b5cf6, #06b6d4);
  color: white;
  animation: chat-badge-pulse 2s ease-in-out infinite;
}

@keyframes chat-badge-pulse {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0.6; }
}

.right-panel-content {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* Main header with view mode toggle (T130) */
.main-header {
  padding: 12px 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.07);
  background: rgba(0, 0, 0, 0.2);
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 16px;
}

/* View mode toggle buttons */
.view-mode-toggle {
  display: flex;
  gap: 0;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.3);
  padding: 2px;
  height: 28px;
}

.view-mode-btn {
  padding: 6px 12px;
  background: transparent;
  border: none;
  border-radius: 3px;
  color: rgba(255, 255, 255, 0.5);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
  white-space: nowrap;
}

.view-mode-btn:hover {
  color: rgba(255, 255, 255, 0.7);
}

.view-mode-btn--active {
  background: rgba(0, 255, 204, 0.1);
  color: #00ffcc;
}

/* Dashboard panels container */
.dashboard-panels {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 0;
  min-height: 0;
}

/* Swimlanes container — full height */
.swimlanes-container {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

/* ── Agent summary row (orchestrator card + active agent mini cards) ── */
.agent-summary-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.07);
  background: rgba(0, 0, 0, 0.15);
  flex-shrink: 0;
  overflow-x: auto;
  min-height: 56px;
}

/* Orchestrator card */
.orch-card {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  background: rgba(0, 255, 204, 0.06);
  border: 1px solid rgba(0, 255, 204, 0.2);
  border-radius: 6px;
  cursor: pointer;
  flex-shrink: 0;
  transition: background 0.15s ease, border-color 0.15s ease;
  min-width: 160px;
}

.orch-card:hover {
  background: rgba(0, 255, 204, 0.12);
  border-color: rgba(0, 255, 204, 0.4);
}

.orch-card--busy {
  border-color: rgba(0, 255, 204, 0.5);
  background: rgba(0, 255, 204, 0.1);
  animation: orch-pulse 2s ease-in-out infinite;
}

@keyframes orch-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(0, 255, 204, 0.15); }
  50% { box-shadow: 0 0 0 4px rgba(0, 255, 204, 0.05); }
}

.orch-card__icon {
  font-size: 18px;
  line-height: 1;
  flex-shrink: 0;
}

.orch-card__body {
  flex: 1;
  min-width: 0;
}

.orch-card__title {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: #00ffcc;
  font-family: ui-monospace, monospace;
}

.orch-card__status {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 10px;
  color: rgba(255, 255, 255, 0.5);
  margin-top: 2px;
  font-family: ui-monospace, monospace;
}

.orch-card__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}

.dot--idle {
  background: rgba(255, 255, 255, 0.3);
}

.dot--busy {
  background: #00ffcc;
  animation: dot-blink 1s ease-in-out infinite;
}

@keyframes dot-blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}

.orch-card__cost {
  font-size: 11px;
  font-weight: 600;
  color: rgba(255, 255, 255, 0.6);
  font-family: ui-monospace, monospace;
  flex-shrink: 0;
}

/* Active agent mini cards */
.agent-mini-card {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 5px 10px;
  background: rgba(59, 130, 246, 0.07);
  border: 1px solid rgba(59, 130, 246, 0.25);
  border-radius: 5px;
  cursor: pointer;
  flex-shrink: 0;
  transition: background 0.15s ease, border-color 0.15s ease;
  min-width: 110px;
  max-width: 160px;
}

.agent-mini-card:hover {
  background: rgba(59, 130, 246, 0.14);
  border-color: rgba(59, 130, 246, 0.5);
}

.agent-mini-card__name {
  font-size: 10px;
  font-weight: 600;
  color: rgba(255, 255, 255, 0.8);
  font-family: ui-monospace, monospace;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.agent-mini-card__phase {
  font-size: 9px;
  color: rgba(59, 130, 246, 0.9);
  font-family: ui-monospace, monospace;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.agent-mini-card__cost {
  font-size: 9px;
  color: rgba(255, 255, 255, 0.4);
  font-family: ui-monospace, monospace;
}

/* Overflow badge */
.agent-overflow-badge {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 4px 10px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 5px;
  font-size: 10px;
  color: rgba(255, 255, 255, 0.4);
  font-family: ui-monospace, monospace;
  flex-shrink: 0;
  cursor: pointer;
}
</style>
