<script setup lang="ts">
import { ref, onMounted, reactive } from "vue";
import { api, type ReposConfig, type RepoEntry, type RepoProbeResult } from "../../api";

const loading = ref(true);
const error = ref<string | null>(null);
const config = ref<ReposConfig | null>(null);
const editingUrl = ref<string | null>(null);

// Add-repo form state
const showAddForm = ref(false);
const addForm = reactive<{
  url: string;
  default_workflow: string;
  model_set: string;
  auto_merge: boolean;
  runtime: string;
  triggers: string[];
  trigger_labels: string;
}>({
  url: "",
  default_workflow: "sdlc",
  model_set: "base",
  auto_merge: true,
  runtime: "native",
  triggers: ["new_issue", "comment_adw", "label"],
  trigger_labels: "tac-master,krypto",
});
const probing = ref(false);
const probeResult = ref<RepoProbeResult | null>(null);
const submitting = ref(false);

const WORKFLOWS = [
  "patch",
  "plan_build",
  "plan_build_test",
  "plan_build_test_review",
  "sdlc",
  "sdlc_zte",
];
const MODEL_SETS = ["base", "heavy"];
const RUNTIMES = ["native", "podman"];
const TRIGGERS = ["new_issue", "comment_adw", "label"];

async function load() {
  loading.value = true;
  error.value = null;
  try {
    config.value = await api.getRepos();
  } catch (e: any) {
    error.value = e?.message ?? String(e);
  } finally {
    loading.value = false;
  }
}

async function probe() {
  if (!addForm.url) return;
  probing.value = true;
  probeResult.value = null;
  try {
    probeResult.value = await api.probeRepo(addForm.url);
  } catch (e: any) {
    probeResult.value = {
      url: addForm.url,
      owner: "",
      repo: "",
      exists: false,
      visibility: "unknown",
      warning: e?.message ?? String(e),
    };
  } finally {
    probing.value = false;
  }
}

async function submitAdd() {
  if (!addForm.url) return;
  submitting.value = true;
  error.value = null;
  try {
    const entry: RepoEntry = {
      url: addForm.url,
      default_workflow: addForm.default_workflow,
      model_set: addForm.model_set,
      auto_merge: addForm.auto_merge,
      runtime: addForm.runtime,
      triggers: [...addForm.triggers],
      trigger_labels: addForm.trigger_labels
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean),
    };
    await api.addRepo(entry);
    await load();
    showAddForm.value = false;
    resetAddForm();
  } catch (e: any) {
    error.value = e?.message ?? String(e);
  } finally {
    submitting.value = false;
  }
}

function resetAddForm() {
  addForm.url = "";
  addForm.default_workflow = "sdlc";
  addForm.model_set = "base";
  addForm.auto_merge = true;
  addForm.runtime = "native";
  addForm.triggers = ["new_issue", "comment_adw", "label"];
  addForm.trigger_labels = "tac-master,krypto";
  probeResult.value = null;
}

async function deleteRepo(url: string) {
  if (!confirm(`Remove ${url} from the allowlist?`)) return;
  try {
    await api.deleteRepo(url);
    await load();
  } catch (e: any) {
    error.value = e?.message ?? String(e);
  }
}

async function toggleField(
  url: string,
  field: keyof RepoEntry,
  value: any,
) {
  try {
    await api.updateRepo(url, { [field]: value } as Partial<RepoEntry>);
    await load();
  } catch (e: any) {
    error.value = e?.message ?? String(e);
  }
}

function toggleTrigger(t: string) {
  const idx = addForm.triggers.indexOf(t);
  if (idx === -1) addForm.triggers.push(t);
  else addForm.triggers.splice(idx, 1);
}

function shortSlug(url: string): string {
  return url.replace("https://github.com/", "").replace(/\.git$/, "");
}

onMounted(load);
</script>

<template>
  <div class="max-w-5xl">
    <!-- Error banner -->
    <div
      v-if="error"
      class="mb-4 p-3 bg-accent-failed/20 border border-accent-failed text-accent-failed text-[11px] rounded"
    >
      {{ error }}
    </div>

    <div v-if="loading" class="text-ink-400 text-[11px]">Loading…</div>

    <template v-else-if="config">
      <!-- Header row with "Add repo" button -->
      <div class="flex items-center justify-between mb-4">
        <div>
          <h2 class="text-ink-100 text-lg font-bold">Allowlisted Repositories</h2>
          <p class="text-ink-400 text-[11px]">
            {{ config.repos.length }} repo(s). Only these are polled and acted on.
          </p>
        </div>
        <button
          @click="showAddForm = !showAddForm"
          class="px-4 py-2 bg-accent-running hover:bg-accent-running/80 text-white text-[11px] uppercase tracking-wider rounded font-bold"
        >
          {{ showAddForm ? "Cancel" : "+ Add Repo" }}
        </button>
      </div>

      <!-- Add form -->
      <div
        v-if="showAddForm"
        class="mb-6 p-5 bg-ink-800 border border-ink-600 rounded"
      >
        <h3 class="text-ink-100 text-sm font-bold mb-3">Add a new repository</h3>
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <!-- URL + probe button -->
          <div class="lg:col-span-2">
            <label class="block text-[11px] text-ink-400 mb-1">Repository URL</label>
            <div class="flex gap-2">
              <input
                v-model="addForm.url"
                @blur="probe"
                type="text"
                placeholder="https://github.com/owner/repo"
                class="flex-1 px-3 py-2 bg-ink-900 border border-ink-600 rounded text-ink-100 text-[12px] focus:border-accent-running focus:outline-none"
              />
              <button
                @click="probe"
                :disabled="probing || !addForm.url"
                class="px-4 py-2 bg-ink-600 hover:bg-ink-400 disabled:opacity-50 text-ink-100 text-[11px] uppercase rounded"
              >
                {{ probing ? "…" : "Probe" }}
              </button>
            </div>
            <div
              v-if="probeResult"
              :class="[
                'mt-2 p-2 rounded text-[11px]',
                probeResult.exists && probeResult.visibility === 'public'
                  ? 'bg-accent-succeeded/10 border border-accent-succeeded/40 text-accent-succeeded'
                  : probeResult.exists
                    ? 'bg-accent-pending/10 border border-accent-pending/40 text-accent-pending'
                    : 'bg-accent-failed/10 border border-accent-failed/40 text-accent-failed',
              ]"
            >
              <div v-if="probeResult.exists">
                <span class="font-bold uppercase">{{ probeResult.visibility }}</span>
                — {{ probeResult.owner }}/{{ probeResult.repo }}
                <span v-if="probeResult.default_branch">
                  · default branch: {{ probeResult.default_branch }}
                </span>
                <span v-if="probeResult.stars !== undefined"> · ★{{ probeResult.stars }}</span>
                <div v-if="probeResult.description" class="mt-1 text-ink-200">
                  {{ probeResult.description }}
                </div>
                <div v-if="probeResult.warning" class="mt-1">
                  ⚠ {{ probeResult.warning }}
                </div>
              </div>
              <div v-else>
                ✗ {{ probeResult.warning ?? "Repo not found" }}
              </div>
            </div>
          </div>

          <!-- Workflow -->
          <div>
            <label class="block text-[11px] text-ink-400 mb-1">Default workflow</label>
            <select
              v-model="addForm.default_workflow"
              class="w-full px-3 py-2 bg-ink-900 border border-ink-600 rounded text-ink-100 text-[12px]"
            >
              <option v-for="w in WORKFLOWS" :key="w" :value="w">{{ w }}</option>
            </select>
          </div>

          <!-- Model set -->
          <div>
            <label class="block text-[11px] text-ink-400 mb-1">Model set</label>
            <select
              v-model="addForm.model_set"
              class="w-full px-3 py-2 bg-ink-900 border border-ink-600 rounded text-ink-100 text-[12px]"
            >
              <option v-for="m in MODEL_SETS" :key="m" :value="m">{{ m }}</option>
            </select>
          </div>

          <!-- Runtime -->
          <div>
            <label class="block text-[11px] text-ink-400 mb-1">Runtime</label>
            <select
              v-model="addForm.runtime"
              class="w-full px-3 py-2 bg-ink-900 border border-ink-600 rounded text-ink-100 text-[12px]"
            >
              <option v-for="r in RUNTIMES" :key="r" :value="r">{{ r }}</option>
            </select>
          </div>

          <!-- Auto-merge -->
          <div>
            <label class="block text-[11px] text-ink-400 mb-1">Auto-merge</label>
            <label class="inline-flex items-center gap-2 mt-1 text-[12px] text-ink-100">
              <input
                type="checkbox"
                v-model="addForm.auto_merge"
                class="accent-accent-running"
              />
              Enabled (required for sdlc_zte)
            </label>
          </div>

          <!-- Triggers -->
          <div class="lg:col-span-2">
            <label class="block text-[11px] text-ink-400 mb-1">Triggers</label>
            <div class="flex flex-wrap gap-3">
              <label
                v-for="t in TRIGGERS"
                :key="t"
                class="inline-flex items-center gap-2 text-[12px] text-ink-100"
              >
                <input
                  type="checkbox"
                  :checked="addForm.triggers.includes(t)"
                  @change="toggleTrigger(t)"
                  class="accent-accent-running"
                />
                {{ t }}
              </label>
            </div>
          </div>

          <!-- Trigger labels -->
          <div class="lg:col-span-2">
            <label class="block text-[11px] text-ink-400 mb-1">
              Trigger labels (comma-separated)
            </label>
            <input
              v-model="addForm.trigger_labels"
              type="text"
              class="w-full px-3 py-2 bg-ink-900 border border-ink-600 rounded text-ink-100 text-[12px]"
            />
          </div>
        </div>

        <div class="mt-4 flex gap-2 justify-end">
          <button
            @click="showAddForm = false; resetAddForm()"
            class="px-4 py-2 bg-ink-600 hover:bg-ink-400 text-ink-100 text-[11px] uppercase rounded"
          >
            Cancel
          </button>
          <button
            @click="submitAdd"
            :disabled="submitting || !addForm.url"
            class="px-4 py-2 bg-accent-succeeded hover:bg-accent-succeeded/80 disabled:opacity-50 text-white text-[11px] uppercase rounded font-bold"
          >
            {{ submitting ? "Saving…" : "Save Repo" }}
          </button>
        </div>
      </div>

      <!-- Repo list -->
      <div class="space-y-3">
        <div
          v-for="r in config.repos"
          :key="r.url"
          class="p-4 bg-ink-800 border border-ink-600 rounded"
        >
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-2 flex-wrap">
                <span class="text-ink-100 font-bold">{{ shortSlug(r.url) }}</span>
                <span
                  v-if="r.self"
                  class="text-[10px] uppercase px-1.5 py-0.5 bg-accent-self/20 text-accent-self rounded"
                  >self</span
                >
                <span
                  class="text-[10px] uppercase px-1.5 py-0.5 bg-ink-600 text-ink-100 rounded"
                >
                  {{ r.default_workflow ?? "sdlc" }}
                </span>
                <span
                  class="text-[10px] uppercase px-1.5 py-0.5 bg-ink-600 text-ink-100 rounded"
                >
                  {{ r.model_set ?? "base" }}
                </span>
                <span
                  v-if="r.auto_merge"
                  class="text-[10px] uppercase px-1.5 py-0.5 bg-accent-succeeded/20 text-accent-succeeded rounded"
                  >auto-merge</span
                >
                <span
                  class="text-[10px] uppercase px-1.5 py-0.5 bg-ink-600 text-ink-100 rounded"
                >
                  {{ r.runtime ?? "native" }}
                </span>
              </div>
              <a
                :href="r.url"
                target="_blank"
                class="text-[11px] text-ink-400 hover:text-accent-running break-all"
              >
                {{ r.url }}
              </a>
              <div class="mt-2 text-[11px] text-ink-400">
                triggers:
                <span class="text-ink-200">{{ (r.triggers ?? []).join(", ") || "none" }}</span>
                <span v-if="r.trigger_labels?.length">
                  · labels:
                  <span class="text-ink-200">{{ r.trigger_labels.join(", ") }}</span>
                </span>
              </div>
            </div>
            <button
              @click="deleteRepo(r.url)"
              class="px-3 py-1 bg-accent-failed/20 hover:bg-accent-failed/40 text-accent-failed text-[10px] uppercase rounded"
            >
              Remove
            </button>
          </div>
        </div>
      </div>

      <p class="mt-6 text-[10px] text-ink-400 italic">
        Remember to click "Restart Daemon" at the top after making changes for them
        to take effect on the running daemon.
      </p>
    </template>
  </div>
</template>
