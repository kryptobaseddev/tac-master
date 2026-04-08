<script setup lang="ts">
import { computed } from "vue";
import type { RunSummary } from "../types";

const props = defineProps<{
  runs: Map<string, RunSummary>;
  selectedRepo: string | null;
}>();

const filtered = computed(() => {
  const all = Array.from(props.runs.values());
  const sorted = all.sort((a, b) => {
    const pA = a.status === "running" || a.status === "pending" ? 0 : 1;
    const pB = b.status === "running" || b.status === "pending" ? 0 : 1;
    if (pA !== pB) return pA - pB;
    return (b.started_at ?? 0) - (a.started_at ?? 0);
  });
  return props.selectedRepo
    ? sorted.filter((r) => r.repo_url === props.selectedRepo)
    : sorted;
});

function statusClass(s: string): string {
  return (
    {
      pending: "text-accent-pending",
      running: "text-accent-running",
      succeeded: "text-accent-succeeded",
      failed: "text-accent-failed",
      aborted: "text-accent-failed",
    }[s] ?? "text-ink-400"
  );
}

function fmtDuration(start: number | null | undefined, end: number | null | undefined): string {
  if (!start) return "—";
  const e = end ?? Math.floor(Date.now() / 1000);
  const d = e - start;
  if (d < 60) return `${d}s`;
  if (d < 3600) return `${Math.floor(d / 60)}m ${d % 60}s`;
  return `${Math.floor(d / 3600)}h ${Math.floor((d % 3600) / 60)}m`;
}

function shortRepo(url: string): string {
  return url.replace("https://github.com/", "").replace(/\.git$/, "");
}
</script>

<template>
  <section class="p-4 border-t border-ink-800">
    <h2 class="text-ink-200 uppercase tracking-wider text-xs mb-3">
      Runs {{ selectedRepo ? `· ${shortRepo(selectedRepo)}` : "" }}
    </h2>
    <div v-if="filtered.length === 0" class="text-ink-400 text-xs italic">
      No runs yet.
    </div>
    <ul class="space-y-1">
      <li
        v-for="r in filtered"
        :key="r.adw_id"
        class="flex items-center gap-3 bg-ink-800 rounded px-3 py-2 hover:bg-ink-600/30"
      >
        <span :class="['font-bold text-[11px] uppercase w-20', statusClass(r.status)]">
          {{ r.status }}
        </span>
        <span class="font-mono text-ink-100 text-[11px] w-16">{{ r.adw_id }}</span>
        <span class="text-ink-400 text-[11px]">#{{ r.issue_number }}</span>
        <span class="text-ink-100 text-[11px]">{{ r.workflow }}</span>
        <span class="text-ink-400 text-[11px]">({{ r.model_set }})</span>
        <span class="text-ink-400 text-[11px] flex-1 truncate">
          {{ shortRepo(r.repo_url) }}
        </span>
        <span class="text-ink-400 text-[11px]">
          {{ fmtDuration(r.started_at, r.ended_at) }}
        </span>
        <span class="text-ink-100 text-[11px]">
          {{ r.tokens_used >= 1000 ? (r.tokens_used / 1000).toFixed(1) + "k" : r.tokens_used }}
          toks
        </span>
      </li>
    </ul>
  </section>
</template>
