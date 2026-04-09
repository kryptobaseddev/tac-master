<script setup lang="ts">
import { ref } from "vue";
import ReposConfigTab from "./config/ReposConfigTab.vue";
import BudgetsConfigTab from "./config/BudgetsConfigTab.vue";
import PoliciesConfigTab from "./config/PoliciesConfigTab.vue";
import ModelPricesConfigTab from "./config/ModelPricesConfigTab.vue";
import { api } from "../api";

type Tab = "repos" | "budgets" | "policies" | "prices";
const activeTab = ref<Tab>("repos");

const restarting = ref(false);
const restartMessage = ref<{ kind: "ok" | "err"; text: string } | null>(null);

async function restartDaemon() {
  if (restarting.value) return;
  restarting.value = true;
  restartMessage.value = null;
  try {
    const result = await api.restartDaemon();
    if (result.ok) {
      restartMessage.value = {
        kind: "ok",
        text: "Daemon restarted — config changes are live.",
      };
    } else {
      restartMessage.value = {
        kind: "err",
        text: `Restart failed: ${result.output.slice(0, 200)}`,
      };
    }
  } catch (e: any) {
    restartMessage.value = {
      kind: "err",
      text: `Restart error: ${e?.message ?? e}`,
    };
  } finally {
    restarting.value = false;
    setTimeout(() => (restartMessage.value = null), 8000);
  }
}

const tabs: Array<{ id: Tab; label: string; hint: string }> = [
  { id: "repos", label: "Repos", hint: "Allowlisted repositories" },
  { id: "budgets", label: "Budgets", hint: "Cost + concurrency caps" },
  { id: "policies", label: "Policies", hint: "Safety rules + workflow limits" },
  { id: "prices", label: "Model Prices", hint: "Per-model $/token" },
];
</script>

<template>
  <div class="flex flex-col h-full">
    <!-- Config sub-tabs + restart button -->
    <header class="border-b border-ink-800 bg-ink-900 px-6 py-4 flex items-start justify-between">
      <div>
        <h1 class="text-ink-100 text-xl font-bold mb-1">Configuration</h1>
        <p class="text-ink-400 text-[11px]">
          Changes are saved to <code class="text-ink-200">/srv/tac-master/config/*.yaml</code>.
          Restart the daemon to apply them.
        </p>
        <nav class="mt-3 flex gap-1">
          <button
            v-for="tab in tabs"
            :key="tab.id"
            @click="activeTab = tab.id"
            :class="[
              'px-4 py-2 rounded-t-md text-[11px] uppercase tracking-wider transition',
              activeTab === tab.id
                ? 'bg-ink-800 text-ink-100 border-b-2 border-accent-self'
                : 'text-ink-400 hover:text-ink-100 hover:bg-ink-800/50',
            ]"
            :title="tab.hint"
          >
            {{ tab.label }}
          </button>
        </nav>
      </div>
      <div class="flex flex-col items-end gap-2">
        <button
          @click="restartDaemon"
          :disabled="restarting"
          class="px-4 py-2 bg-accent-self hover:bg-accent-self/80 disabled:opacity-50 disabled:cursor-not-allowed text-white text-[11px] uppercase tracking-wider rounded font-bold transition"
        >
          {{ restarting ? "Restarting…" : "Restart Daemon" }}
        </button>
        <div
          v-if="restartMessage"
          :class="[
            'text-[11px] max-w-xs text-right',
            restartMessage.kind === 'ok'
              ? 'text-accent-succeeded'
              : 'text-accent-failed',
          ]"
        >
          {{ restartMessage.text }}
        </div>
      </div>
    </header>

    <!-- Active tab content -->
    <div class="flex-1 overflow-auto p-6">
      <ReposConfigTab v-if="activeTab === 'repos'" />
      <BudgetsConfigTab v-else-if="activeTab === 'budgets'" />
      <PoliciesConfigTab v-else-if="activeTab === 'policies'" />
      <ModelPricesConfigTab v-else-if="activeTab === 'prices'" />
    </div>
  </div>
</template>
