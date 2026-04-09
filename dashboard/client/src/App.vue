<script setup lang="ts">
import { computed, ref } from "vue";
import { useWebSocket } from "./composables/useWebSocket";
import RepoStatusBoard from "./components/RepoStatusBoard.vue";
import RunsPanel from "./components/RunsPanel.vue";
import EventStream from "./components/EventStream.vue";
import ConfigPage from "./components/ConfigPage.vue";

// Derive WebSocket URL from the current browser location so it works
// whether the user visits http://localhost:4000, http://10.0.10.22:4000,
// or through a reverse proxy. Falls back to VITE_WS_URL env if set.
const wsUrl =
  (import.meta.env.VITE_WS_URL as string | undefined) ??
  `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/stream`;
const { events, runs, repos, isConnected, error } = useWebSocket(wsUrl);

type Tab = "dashboard" | "config";
const activeTab = ref<Tab>("dashboard");
const selectedRepo = ref<string | null>(null);

const liveRuns = computed(
  () =>
    Array.from(runs.value.values()).filter(
      (r) => r.status === "running" || r.status === "pending",
    ).length,
);

const totalCostToday = computed(() =>
  Array.from(repos.value.values()).reduce((s, r) => s + r.cost_today_usd, 0),
);

const totalTokensToday = computed(() =>
  Array.from(repos.value.values()).reduce((s, r) => s + r.tokens_today, 0),
);

function fmtTokens(t: number): string {
  if (t >= 1_000_000) return `${(t / 1_000_000).toFixed(2)}M`;
  if (t >= 1_000) return `${(t / 1_000).toFixed(1)}k`;
  return String(t);
}
</script>

<template>
  <div class="h-full flex flex-col">
    <!-- Header bar -->
    <header
      class="flex items-center justify-between px-5 py-3 border-b border-ink-800 bg-ink-900"
    >
      <div class="flex items-center gap-4">
        <div class="text-lg font-bold text-ink-100">
          <span class="text-accent-self">tac-</span>master
        </div>
        <span
          :class="[
            'w-2 h-2 rounded-full',
            isConnected ? 'bg-accent-succeeded' : 'bg-accent-failed',
          ]"
        />
        <span class="text-[11px] text-ink-400">
          {{ isConnected ? "connected" : "reconnecting…" }}
        </span>
        <nav class="ml-4 flex gap-1">
          <button
            @click="activeTab = 'dashboard'"
            :class="[
              'px-3 py-1 rounded text-[11px] uppercase tracking-wider transition',
              activeTab === 'dashboard'
                ? 'bg-ink-800 text-ink-100'
                : 'text-ink-400 hover:text-ink-100',
            ]"
          >
            Dashboard
          </button>
          <button
            @click="activeTab = 'config'"
            :class="[
              'px-3 py-1 rounded text-[11px] uppercase tracking-wider transition',
              activeTab === 'config'
                ? 'bg-ink-800 text-ink-100'
                : 'text-ink-400 hover:text-ink-100',
            ]"
          >
            Config
          </button>
        </nav>
      </div>
      <div class="flex items-center gap-5 text-[11px]">
        <div>
          <span class="text-ink-400">live:</span>
          <span class="text-accent-running font-bold ml-1">{{ liveRuns }}</span>
        </div>
        <div>
          <span class="text-ink-400">repos:</span>
          <span class="text-ink-100 font-bold ml-1">{{ repos.size }}</span>
        </div>
        <div>
          <span class="text-ink-400">tokens/day:</span>
          <span class="text-ink-100 font-bold ml-1">
            {{ fmtTokens(totalTokensToday) }}
          </span>
        </div>
        <div>
          <span class="text-ink-400">cost/day:</span>
          <span class="text-ink-100 font-bold ml-1">
            ${{ totalCostToday.toFixed(2) }}
          </span>
        </div>
      </div>
    </header>

    <!-- Error banner -->
    <div
      v-if="error"
      class="bg-accent-failed/20 border-b border-accent-failed text-accent-failed px-4 py-1 text-[11px]"
    >
      {{ error }}
    </div>

    <!-- Main content area — switches between dashboard and config -->
    <main class="flex-1 flex overflow-hidden">
      <template v-if="activeTab === 'dashboard'">
        <!-- Left: repos board + runs -->
        <aside class="w-[540px] shrink-0 border-r border-ink-800 overflow-auto">
          <RepoStatusBoard
            :repos="repos"
            :selected-repo="selectedRepo"
            @select="selectedRepo = $event"
          />
          <RunsPanel :runs="runs" :selected-repo="selectedRepo" />
        </aside>
        <!-- Right: event stream -->
        <div class="flex-1 overflow-hidden">
          <EventStream :events="events" :selected-repo="selectedRepo" />
        </div>
      </template>
      <template v-else>
        <ConfigPage class="flex-1 overflow-auto" />
      </template>
    </main>
  </div>
</template>
