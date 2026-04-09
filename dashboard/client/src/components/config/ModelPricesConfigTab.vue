<script setup lang="ts">
import { ref, onMounted, computed } from "vue";
import { api, type ModelPricesConfig, type ModelPrice } from "../../api";

const loading = ref(true);
const saving = ref(false);
const error = ref<string | null>(null);
const success = ref<string | null>(null);
const config = ref<ModelPricesConfig | null>(null);

const newModelName = ref("");
const newModelPrice = ref<ModelPrice>({
  input: 3.0,
  output: 15.0,
  cache_write: 3.75,
  cache_read: 0.3,
});

const sortedModels = computed(() => {
  if (!config.value?.prices) return [];
  return Object.keys(config.value.prices).sort();
});

async function load() {
  loading.value = true;
  error.value = null;
  try {
    config.value = await api.getModelPrices();
    if (!config.value.prices) config.value.prices = {};
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
    await api.putModelPrices(config.value);
    success.value = "Saved. Prices apply to future runs only.";
    setTimeout(() => (success.value = null), 6000);
  } catch (e: any) {
    error.value = e?.message ?? String(e);
  } finally {
    saving.value = false;
  }
}

function addModel() {
  if (!config.value) return;
  const name = newModelName.value.trim();
  if (!name) return;
  if (config.value.prices[name]) {
    error.value = `Model "${name}" already exists`;
    return;
  }
  config.value.prices[name] = { ...newModelPrice.value };
  newModelName.value = "";
}

function removeModel(name: string) {
  if (!config.value) return;
  if (name === "default") {
    error.value = "Cannot remove the 'default' fallback pricing";
    return;
  }
  if (!confirm(`Remove pricing for ${name}?`)) return;
  delete config.value.prices[name];
}

onMounted(load);
</script>

<template>
  <div class="max-w-5xl">
    <div v-if="error" class="mb-4 p-3 bg-accent-failed/20 border border-accent-failed text-accent-failed text-[11px] rounded">{{ error }}</div>
    <div v-if="success" class="mb-4 p-3 bg-accent-succeeded/20 border border-accent-succeeded text-accent-succeeded text-[11px] rounded">{{ success }}</div>

    <div v-if="loading" class="text-ink-400">Loading…</div>

    <template v-else-if="config">
      <div class="flex items-center justify-between mb-4">
        <div>
          <h2 class="text-ink-100 text-lg font-bold">Model Pricing</h2>
          <p class="text-ink-400 text-[11px]">
            USD per 1M tokens. Used by token_tracker to price runs when Claude Code
            doesn't return <code class="text-ink-200">total_cost_usd</code> directly.
            The <code class="text-accent-self">default</code> row is the fallback for
            unknown models.
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

      <!-- Pricing table -->
      <section class="mb-6 bg-ink-800 border border-ink-600 rounded overflow-hidden">
        <table class="w-full text-[12px]">
          <thead class="bg-ink-900 text-ink-400 uppercase text-[10px]">
            <tr>
              <th class="text-left px-3 py-2">Model</th>
              <th class="text-right px-3 py-2">Input ($/1M)</th>
              <th class="text-right px-3 py-2">Output ($/1M)</th>
              <th class="text-right px-3 py-2">Cache write ($/1M)</th>
              <th class="text-right px-3 py-2">Cache read ($/1M)</th>
              <th class="w-20"></th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="name in sortedModels"
              :key="name"
              class="border-t border-ink-600"
              :class="name === 'default' ? 'bg-ink-900/50' : ''"
            >
              <td class="px-3 py-2 text-ink-100 font-mono">
                {{ name }}
                <span v-if="name === 'default'" class="text-[9px] text-accent-self ml-2 uppercase">fallback</span>
              </td>
              <td class="px-3 py-2 text-right">
                <input
                  type="number"
                  step="0.01"
                  v-model.number="config!.prices[name].input"
                  class="w-24 px-2 py-1 bg-ink-900 border border-ink-600 rounded text-ink-100 text-right"
                />
              </td>
              <td class="px-3 py-2 text-right">
                <input
                  type="number"
                  step="0.01"
                  v-model.number="config!.prices[name].output"
                  class="w-24 px-2 py-1 bg-ink-900 border border-ink-600 rounded text-ink-100 text-right"
                />
              </td>
              <td class="px-3 py-2 text-right">
                <input
                  type="number"
                  step="0.01"
                  v-model.number="config!.prices[name].cache_write"
                  class="w-24 px-2 py-1 bg-ink-900 border border-ink-600 rounded text-ink-100 text-right"
                />
              </td>
              <td class="px-3 py-2 text-right">
                <input
                  type="number"
                  step="0.01"
                  v-model.number="config!.prices[name].cache_read"
                  class="w-24 px-2 py-1 bg-ink-900 border border-ink-600 rounded text-ink-100 text-right"
                />
              </td>
              <td class="px-3 py-2 text-right">
                <button
                  v-if="name !== 'default'"
                  @click="removeModel(name)"
                  class="text-[10px] text-accent-failed hover:underline uppercase"
                >
                  Remove
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </section>

      <!-- Add new model -->
      <section class="p-5 bg-ink-800 border border-ink-600 rounded">
        <h3 class="text-ink-100 text-sm font-bold mb-3">Add model</h3>
        <div class="grid grid-cols-5 gap-3">
          <input
            v-model="newModelName"
            type="text"
            placeholder="model-id (e.g. claude-opus-4-7)"
            class="col-span-5 px-3 py-2 bg-ink-900 border border-ink-600 rounded text-ink-100 text-[12px] font-mono"
          />
          <div>
            <label class="block text-[10px] text-ink-400">Input</label>
            <input
              type="number"
              step="0.01"
              v-model.number="newModelPrice.input"
              class="w-full px-2 py-1 bg-ink-900 border border-ink-600 rounded text-ink-100 text-[12px]"
            />
          </div>
          <div>
            <label class="block text-[10px] text-ink-400">Output</label>
            <input
              type="number"
              step="0.01"
              v-model.number="newModelPrice.output"
              class="w-full px-2 py-1 bg-ink-900 border border-ink-600 rounded text-ink-100 text-[12px]"
            />
          </div>
          <div>
            <label class="block text-[10px] text-ink-400">Cache write</label>
            <input
              type="number"
              step="0.01"
              v-model.number="newModelPrice.cache_write"
              class="w-full px-2 py-1 bg-ink-900 border border-ink-600 rounded text-ink-100 text-[12px]"
            />
          </div>
          <div>
            <label class="block text-[10px] text-ink-400">Cache read</label>
            <input
              type="number"
              step="0.01"
              v-model.number="newModelPrice.cache_read"
              class="w-full px-2 py-1 bg-ink-900 border border-ink-600 rounded text-ink-100 text-[12px]"
            />
          </div>
          <div class="flex items-end">
            <button
              @click="addModel"
              :disabled="!newModelName.trim()"
              class="w-full px-3 py-2 bg-accent-running hover:bg-accent-running/80 disabled:opacity-50 text-white text-[11px] uppercase rounded"
            >
              + Add
            </button>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>
