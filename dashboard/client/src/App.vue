<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import { useOrchestratorStore } from "./stores/orchestratorStore";

// Layout shell
import CommandCenterLayout from "./components/CommandCenterLayout.vue";

// Header + sidebar + status
import HeaderBar from "./components/HeaderBar.vue";
import RepoSidebar from "./components/RepoSidebar.vue";
import StatusBar from "./components/StatusBar.vue";

// Main panels (center column)
import ActiveAgentsPanel from "./components/ActiveAgentsPanel.vue";
import PipelineFlow from "./components/PipelineFlow.vue";
import IssueDetails from "./components/IssueDetails.vue";

// Right panel
import LiveExecutionPanel from "./components/LiveExecutionPanel.vue";

// Sidebar — CLEO task tree
import EpicTaskTree from "./components/command-center/EpicTaskTree.vue";

// Legacy pages (repos, config)
import RepoBoard from "./components/RepoBoard.vue";
import ConfigPage from "./components/ConfigPage.vue";

// ── Store ─────────────────────────────────────────────────────────
const store = useOrchestratorStore();

// ── Tab routing ───────────────────────────────────────────────────
type Tab = "dashboard" | "repos" | "config";
const activeTab = ref<Tab>("dashboard");

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

// ── Initialize WebSocket + fetch ──────────────────────────────────
onMounted(() => {
  store.initialize();

  // Default currentRepoUrl to first repo once data arrives
  const unsub = store.$subscribe(() => {
    if (!currentRepoUrl.value && store.repos.length > 0) {
      currentRepoUrl.value = store.repos[0].url;
      unsub();
    }
  });
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

    <!-- ── Left sidebar: repo list + CLEO task tree ────────── -->
    <template #sidebar>
      <RepoSidebar
        :selected-repo-url="currentRepoUrl"
        :active-tab="activeTab"
        @select-repo="handleRepoSelect"
        @navigate="activeTab = ($event as Tab)"
      />
      <EpicTaskTree />
    </template>

    <!-- ── Center main area ───────────────────────────────── -->
    <template #main>
      <!-- Dashboard view: panels -->
      <template v-if="activeTab === 'dashboard'">
        <ActiveAgentsPanel />
        <PipelineFlow />
        <IssueDetails />
      </template>

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

    <!-- ── Right sidebar: live execution feed ────────────── -->
    <template #right>
      <LiveExecutionPanel />
    </template>

    <!-- ── Status bar ─────────────────────────────────────── -->
    <template #statusbar>
      <StatusBar />
    </template>
  </CommandCenterLayout>
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
</style>
