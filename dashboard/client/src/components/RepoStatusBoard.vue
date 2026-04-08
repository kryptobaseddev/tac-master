<script setup lang="ts">
import { computed } from "vue";
import type { RepoStatus } from "../types";

const props = defineProps<{
  repos: Map<string, RepoStatus>;
  selectedRepo: string | null;
}>();

const emit = defineEmits<{
  (e: "select", url: string | null): void;
}>();

const sorted = computed(() =>
  Array.from(props.repos.values()).sort((a, b) => {
    if (a.is_self !== b.is_self) return a.is_self ? -1 : 1;
    return a.slug.localeCompare(b.slug);
  }),
);

function fmtTime(ts: number | null): string {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

function fmtCost(c: number): string {
  if (c >= 1) return `$${c.toFixed(2)}`;
  if (c > 0) return `$${c.toFixed(4)}`;
  return "$0";
}

function fmtTokens(t: number): string {
  if (t >= 1_000_000) return `${(t / 1_000_000).toFixed(2)}M`;
  if (t >= 1_000) return `${(t / 1_000).toFixed(1)}k`;
  return String(t);
}

function statusColor(r: RepoStatus): string {
  if (r.active_runs > 0) return "border-accent-running";
  if (r.failed_today > 0 && r.completed_today === 0) return "border-accent-failed";
  if (r.completed_today > 0) return "border-accent-succeeded";
  return "border-ink-600";
}

function toggle(url: string): void {
  emit("select", props.selectedRepo === url ? null : url);
}
</script>

<template>
  <section class="p-4">
    <h2 class="text-ink-200 uppercase tracking-wider text-xs mb-3">Repositories</h2>
    <div v-if="sorted.length === 0" class="text-ink-400 text-xs italic">
      Waiting for repos… ensure tac_master.sqlite is readable.
    </div>
    <div class="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-3">
      <button
        v-for="r in sorted"
        :key="r.url"
        @click="toggle(r.url)"
        :class="[
          'text-left rounded-md border-l-4 bg-ink-800 px-3 py-2 transition',
          'hover:bg-ink-600/30',
          statusColor(r),
          selectedRepo === r.url ? 'ring-1 ring-ink-200' : '',
        ]"
      >
        <div class="flex items-start justify-between gap-2">
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-2">
              <span class="text-ink-100 font-semibold truncate">{{ r.slug }}</span>
              <span
                v-if="r.is_self"
                class="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-accent-self/20 text-accent-self"
                >self</span
              >
              <span
                v-if="r.auto_merge"
                class="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-ink-600 text-ink-100"
                >zte</span
              >
            </div>
            <div class="text-[11px] text-ink-400 mt-0.5">
              {{ r.default_workflow }} · {{ r.model_set }}
            </div>
          </div>
          <div
            v-if="r.active_runs > 0"
            class="shrink-0 text-[11px] bg-accent-running/20 text-accent-running px-2 py-0.5 rounded"
          >
            {{ r.active_runs }} live
          </div>
        </div>
        <div class="mt-2 grid grid-cols-4 gap-2 text-[11px]">
          <div>
            <div class="text-ink-400">ok</div>
            <div class="text-accent-succeeded">{{ r.completed_today }}</div>
          </div>
          <div>
            <div class="text-ink-400">fail</div>
            <div class="text-accent-failed">{{ r.failed_today }}</div>
          </div>
          <div>
            <div class="text-ink-400">toks</div>
            <div class="text-ink-100">{{ fmtTokens(r.tokens_today) }}</div>
          </div>
          <div>
            <div class="text-ink-400">cost</div>
            <div class="text-ink-100">{{ fmtCost(r.cost_today_usd) }}</div>
          </div>
        </div>
        <div class="mt-1 text-[10px] text-ink-400">
          last: {{ fmtTime(r.last_activity_at) }}
        </div>
      </button>
    </div>
  </section>
</template>
