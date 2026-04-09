<script setup lang="ts">
import { ref, onMounted } from "vue";
import { api, type PoliciesConfig } from "../../api";

const loading = ref(true);
const saving = ref(false);
const error = ref<string | null>(null);
const success = ref<string | null>(null);
const config = ref<PoliciesConfig | null>(null);

// For text-area editing of array fields
const protectedBranchesText = ref("");
const forbiddenPathsText = ref("");

async function load() {
  loading.value = true;
  error.value = null;
  try {
    config.value = await api.getPolicies();
    if (!config.value.safety) config.value.safety = {};
    if (!config.value.workflows) config.value.workflows = {};
    if (!config.value.self_improvement) {
      config.value.self_improvement = {};
    }
    if (!config.value.self_improvement.post_merge_health_check) {
      config.value.self_improvement.post_merge_health_check = {};
    }

    protectedBranchesText.value = (config.value.safety.protected_branches ?? []).join(
      "\n",
    );
    forbiddenPathsText.value = (config.value.safety.forbidden_paths ?? []).join("\n");
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
    // Parse the text areas back into arrays
    if (config.value.safety) {
      config.value.safety.protected_branches = protectedBranchesText.value
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean);
      config.value.safety.forbidden_paths = forbiddenPathsText.value
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean);
    }
    await api.putPolicies(config.value);
    success.value = "Saved. Restart daemon to apply.";
    setTimeout(() => (success.value = null), 6000);
  } catch (e: any) {
    error.value = e?.message ?? String(e);
  } finally {
    saving.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="max-w-4xl">
    <div v-if="error" class="mb-4 p-3 bg-accent-failed/20 border border-accent-failed text-accent-failed text-[11px] rounded">{{ error }}</div>
    <div v-if="success" class="mb-4 p-3 bg-accent-succeeded/20 border border-accent-succeeded text-accent-succeeded text-[11px] rounded">{{ success }}</div>

    <div v-if="loading" class="text-ink-400">Loading…</div>

    <template v-else-if="config">
      <div class="flex items-center justify-between mb-4">
        <div>
          <h2 class="text-ink-100 text-lg font-bold">Execution Policies</h2>
          <p class="text-ink-400 text-[11px]">
            Safety rules, forbidden paths, per-workflow rate limits, and
            self-improvement guardrails.
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

      <!-- Safety -->
      <section class="mb-6 p-5 bg-ink-800 border border-ink-600 rounded">
        <h3 class="text-ink-100 text-sm font-bold mb-3">Safety</h3>
        <div class="space-y-3">
          <div>
            <label class="block text-[11px] text-ink-400 mb-1">
              Protected branches (one per line — never push directly)
            </label>
            <textarea
              v-model="protectedBranchesText"
              rows="3"
              class="w-full px-3 py-2 bg-ink-900 border border-ink-600 rounded text-ink-100 text-[12px] font-mono"
            ></textarea>
          </div>
          <div>
            <label class="block text-[11px] text-ink-400 mb-1">
              Forbidden paths (one per line, glob patterns — never modify)
            </label>
            <textarea
              v-model="forbiddenPathsText"
              rows="5"
              class="w-full px-3 py-2 bg-ink-900 border border-ink-600 rounded text-ink-100 text-[12px] font-mono"
            ></textarea>
          </div>
          <div class="grid grid-cols-2 gap-4">
            <div>
              <label class="block text-[11px] text-ink-400 mb-1">Max files per PR</label>
              <input
                type="number"
                v-model.number="config.safety!.max_files_per_pr"
                class="w-full px-3 py-2 bg-ink-900 border border-ink-600 rounded text-ink-100 text-[12px]"
              />
            </div>
            <div>
              <label class="block text-[11px] text-ink-400 mb-1">Max diff lines per PR</label>
              <input
                type="number"
                v-model.number="config.safety!.max_diff_lines_per_pr"
                class="w-full px-3 py-2 bg-ink-900 border border-ink-600 rounded text-ink-100 text-[12px]"
              />
            </div>
          </div>
          <label class="inline-flex items-center gap-2 text-[12px] text-ink-100">
            <input
              type="checkbox"
              v-model="config.safety!.require_tests_pass"
              class="accent-accent-running"
            />
            Require tests to pass before progressing past test phase
          </label>
        </div>
      </section>

      <!-- Self-improvement -->
      <section class="mb-6 p-5 bg-ink-800 border border-ink-600 rounded">
        <h3 class="text-ink-100 text-sm font-bold mb-3">
          Self-improvement (applies to <code class="text-accent-self">self: true</code> repos)
        </h3>
        <div class="space-y-3">
          <label class="inline-flex items-center gap-2 text-[12px] text-ink-100">
            <input
              type="checkbox"
              v-model="config.self_improvement!.allow_auto_merge"
              class="accent-accent-running"
            />
            Allow auto-merge of self-changes
          </label>
          <label class="inline-flex items-center gap-2 text-[12px] text-ink-100">
            <input
              type="checkbox"
              v-model="config.self_improvement!.require_tests_pass"
              class="accent-accent-running"
            />
            Require tests pass for self-changes
          </label>
          <div class="mt-3 p-3 bg-ink-900 border border-ink-600 rounded">
            <div class="text-[11px] text-ink-400 mb-2">Post-merge health check</div>
            <div class="space-y-2">
              <label class="inline-flex items-center gap-2 text-[12px] text-ink-100">
                <input
                  type="checkbox"
                  v-model="config.self_improvement!.post_merge_health_check!.enabled"
                  class="accent-accent-running"
                />
                Enabled
              </label>
              <div class="flex gap-3 items-center">
                <label class="text-[11px] text-ink-400">Timeout minutes</label>
                <input
                  type="number"
                  v-model.number="config.self_improvement!.post_merge_health_check!.timeout_minutes"
                  class="w-24 px-2 py-1 bg-ink-800 border border-ink-600 rounded text-ink-100 text-[12px]"
                />
              </div>
              <label class="inline-flex items-center gap-2 text-[12px] text-ink-100">
                <input
                  type="checkbox"
                  v-model="config.self_improvement!.post_merge_health_check!.revert_on_failure"
                  class="accent-accent-running"
                />
                Auto-revert on failure
              </label>
            </div>
          </div>
        </div>
      </section>

      <!-- Workflow limits -->
      <section class="mb-6 p-5 bg-ink-800 border border-ink-600 rounded">
        <h3 class="text-ink-100 text-sm font-bold mb-3">Per-workflow rate limits</h3>
        <div
          v-for="(w, name) in config.workflows"
          :key="name"
          class="mb-3 p-3 bg-ink-900 border border-ink-600 rounded"
        >
          <div class="text-[11px] text-accent-self uppercase font-bold mb-2">{{ name }}</div>
          <div class="grid grid-cols-3 gap-3">
            <div>
              <label class="block text-[10px] text-ink-400">Max per repo per day</label>
              <input
                type="number"
                v-model.number="w.max_per_repo_per_day"
                class="w-full px-2 py-1 bg-ink-800 border border-ink-600 rounded text-ink-100 text-[12px]"
              />
            </div>
            <div v-if="w.max_files_override !== undefined">
              <label class="block text-[10px] text-ink-400">Max files override</label>
              <input
                type="number"
                v-model.number="w.max_files_override"
                class="w-full px-2 py-1 bg-ink-800 border border-ink-600 rounded text-ink-100 text-[12px]"
              />
            </div>
            <div v-if="w.max_diff_lines_override !== undefined">
              <label class="block text-[10px] text-ink-400">Max diff lines override</label>
              <input
                type="number"
                v-model.number="w.max_diff_lines_override"
                class="w-full px-2 py-1 bg-ink-800 border border-ink-600 rounded text-ink-100 text-[12px]"
              />
            </div>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>
