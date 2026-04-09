<script setup lang="ts">
import { ref, onMounted } from "vue";
import { api, type BudgetsConfig } from "../../api";

const loading = ref(true);
const saving = ref(false);
const error = ref<string | null>(null);
const success = ref<string | null>(null);
const config = ref<BudgetsConfig | null>(null);

async function load() {
  loading.value = true;
  error.value = null;
  try {
    config.value = await api.getBudgets();
    // Initialize nested objects if missing
    if (!config.value.global) config.value.global = {};
    if (!config.value.defaults) config.value.defaults = {};
    if (!config.value.alerts) config.value.alerts = {};
    if (!config.value.repos) config.value.repos = [];
  } catch (e: any) {
    error.value = e?.message ?? String(e);
  } finally {
    loading.value = false;
  }
}

async function save() {
  if (!config.value) return;
  saving.value = true;
  error.value = null;
  success.value = null;
  try {
    await api.putBudgets(config.value);
    success.value = "Saved. Click 'Restart Daemon' at the top to apply.";
    setTimeout(() => (success.value = null), 6000);
  } catch (e: any) {
    error.value = e?.message ?? String(e);
  } finally {
    saving.value = false;
  }
}

function addRepoBudget() {
  if (!config.value) return;
  if (!config.value.repos) config.value.repos = [];
  config.value.repos.push({
    url: "",
    max_tokens_per_day: 1000000,
    max_runs_per_day: 10,
    max_concurrent_runs: 5,
    max_tokens_per_run: 500000,
  });
}

function removeRepoBudget(idx: number) {
  if (!config.value?.repos) return;
  config.value.repos.splice(idx, 1);
}

onMounted(load);
</script>

<template>
  <div class="max-w-4xl">
    <div v-if="error" class="mb-4 p-3 bg-accent-failed/20 border border-accent-failed text-accent-failed text-[11px] rounded">
      {{ error }}
    </div>
    <div v-if="success" class="mb-4 p-3 bg-accent-succeeded/20 border border-accent-succeeded text-accent-succeeded text-[11px] rounded">
      {{ success }}
    </div>

    <div v-if="loading" class="text-ink-400">Loading…</div>

    <template v-else-if="config">
      <div class="flex items-center justify-between mb-4">
        <div>
          <h2 class="text-ink-100 text-lg font-bold">Budgets &amp; Cost Controls</h2>
          <p class="text-ink-400 text-[11px]">
            Token and concurrency caps enforced before every dispatch.
          </p>
        </div>
        <button
          @click="save"
          :disabled="saving"
          class="px-4 py-2 bg-accent-succeeded hover:bg-accent-succeeded/80 disabled:opacity-50 text-white text-[11px] uppercase rounded font-bold"
        >
          {{ saving ? "Saving…" : "Save Changes" }}
        </button>
      </div>

      <!-- Global budget -->
      <section class="mb-6 p-5 bg-ink-800 border border-ink-600 rounded">
        <h3 class="text-ink-100 text-sm font-bold mb-3">Global ceiling</h3>
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="block text-[11px] text-ink-400 mb-1">Max tokens / day</label>
            <input
              type="number"
              v-model.number="config.global!.max_tokens_per_day"
              class="w-full px-3 py-2 bg-ink-900 border border-ink-600 rounded text-ink-100 text-[12px]"
            />
          </div>
          <div>
            <label class="block text-[11px] text-ink-400 mb-1">Max runs / day</label>
            <input
              type="number"
              v-model.number="config.global!.max_runs_per_day"
              class="w-full px-3 py-2 bg-ink-900 border border-ink-600 rounded text-ink-100 text-[12px]"
            />
          </div>
          <div>
            <label class="block text-[11px] text-ink-400 mb-1">Max concurrent runs</label>
            <input
              type="number"
              v-model.number="config.global!.max_concurrent_runs"
              class="w-full px-3 py-2 bg-ink-900 border border-ink-600 rounded text-ink-100 text-[12px]"
            />
          </div>
          <div>
            <label class="block text-[11px] text-ink-400 mb-1">On exceeded</label>
            <select
              v-model="config.global!.on_exceeded"
              class="w-full px-3 py-2 bg-ink-900 border border-ink-600 rounded text-ink-100 text-[12px]"
            >
              <option value="halt">halt</option>
              <option value="warn">warn</option>
              <option value="throttle">throttle</option>
            </select>
          </div>
        </div>
      </section>

      <!-- Per-repo defaults -->
      <section class="mb-6 p-5 bg-ink-800 border border-ink-600 rounded">
        <h3 class="text-ink-100 text-sm font-bold mb-3">Per-repo defaults</h3>
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="block text-[11px] text-ink-400 mb-1">Max tokens / day</label>
            <input
              type="number"
              v-model.number="config.defaults!.max_tokens_per_day"
              class="w-full px-3 py-2 bg-ink-900 border border-ink-600 rounded text-ink-100 text-[12px]"
            />
          </div>
          <div>
            <label class="block text-[11px] text-ink-400 mb-1">Max runs / day</label>
            <input
              type="number"
              v-model.number="config.defaults!.max_runs_per_day"
              class="w-full px-3 py-2 bg-ink-900 border border-ink-600 rounded text-ink-100 text-[12px]"
            />
          </div>
          <div>
            <label class="block text-[11px] text-ink-400 mb-1">Max concurrent runs</label>
            <input
              type="number"
              v-model.number="config.defaults!.max_concurrent_runs"
              class="w-full px-3 py-2 bg-ink-900 border border-ink-600 rounded text-ink-100 text-[12px]"
            />
          </div>
          <div>
            <label class="block text-[11px] text-ink-400 mb-1">Max tokens / single run</label>
            <input
              type="number"
              v-model.number="config.defaults!.max_tokens_per_run"
              class="w-full px-3 py-2 bg-ink-900 border border-ink-600 rounded text-ink-100 text-[12px]"
            />
          </div>
        </div>
      </section>

      <!-- Per-repo overrides -->
      <section class="mb-6 p-5 bg-ink-800 border border-ink-600 rounded">
        <div class="flex items-center justify-between mb-3">
          <h3 class="text-ink-100 text-sm font-bold">Per-repo overrides</h3>
          <button
            @click="addRepoBudget"
            class="px-3 py-1 bg-ink-600 hover:bg-ink-400 text-ink-100 text-[10px] uppercase rounded"
          >
            + Add
          </button>
        </div>
        <div v-if="!config.repos?.length" class="text-ink-400 text-[11px] italic">
          No per-repo overrides. All repos use the defaults above.
        </div>
        <div
          v-for="(r, idx) in config.repos"
          :key="idx"
          class="mb-3 p-3 bg-ink-900 border border-ink-600 rounded"
        >
          <div class="grid grid-cols-2 gap-3 mb-2">
            <div class="col-span-2">
              <label class="block text-[11px] text-ink-400 mb-1">Repo URL</label>
              <input
                type="text"
                v-model="r.url"
                placeholder="https://github.com/owner/repo"
                class="w-full px-3 py-1.5 bg-ink-800 border border-ink-600 rounded text-ink-100 text-[12px]"
              />
            </div>
            <div>
              <label class="block text-[10px] text-ink-400">Tokens/day</label>
              <input
                type="number"
                v-model.number="r.max_tokens_per_day"
                class="w-full px-2 py-1 bg-ink-800 border border-ink-600 rounded text-ink-100 text-[12px]"
              />
            </div>
            <div>
              <label class="block text-[10px] text-ink-400">Runs/day</label>
              <input
                type="number"
                v-model.number="r.max_runs_per_day"
                class="w-full px-2 py-1 bg-ink-800 border border-ink-600 rounded text-ink-100 text-[12px]"
              />
            </div>
            <div>
              <label class="block text-[10px] text-ink-400">Concurrent</label>
              <input
                type="number"
                v-model.number="r.max_concurrent_runs"
                class="w-full px-2 py-1 bg-ink-800 border border-ink-600 rounded text-ink-100 text-[12px]"
              />
            </div>
            <div>
              <label class="block text-[10px] text-ink-400">Tokens/run</label>
              <input
                type="number"
                v-model.number="r.max_tokens_per_run"
                class="w-full px-2 py-1 bg-ink-800 border border-ink-600 rounded text-ink-100 text-[12px]"
              />
            </div>
          </div>
          <button
            @click="removeRepoBudget(idx)"
            class="text-[10px] text-accent-failed hover:underline uppercase"
          >
            Remove
          </button>
        </div>
      </section>

      <!-- Alerts -->
      <section class="mb-6 p-5 bg-ink-800 border border-ink-600 rounded">
        <h3 class="text-ink-100 text-sm font-bold mb-3">Alerts</h3>
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="block text-[11px] text-ink-400 mb-1">Warn at % of budget</label>
            <input
              type="number"
              v-model.number="config.alerts!.warn_at_pct"
              class="w-full px-3 py-2 bg-ink-900 border border-ink-600 rounded text-ink-100 text-[12px]"
            />
          </div>
          <div>
            <label class="block text-[11px] text-ink-400 mb-1">Halt notify as issue</label>
            <label class="inline-flex items-center gap-2 mt-1 text-[12px] text-ink-100">
              <input
                type="checkbox"
                v-model="config.alerts!.halt_notify_issue"
                class="accent-accent-running"
              />
              File GitHub issue on halt
            </label>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>
