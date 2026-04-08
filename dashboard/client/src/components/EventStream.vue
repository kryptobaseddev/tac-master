<script setup lang="ts">
import { computed, ref, watch, nextTick } from "vue";
import type { HookEvent } from "../types";

const props = defineProps<{
  events: HookEvent[];
  selectedRepo: string | null;
}>();

const stickToBottom = ref(true);
const scrollEl = ref<HTMLElement | null>(null);

const filtered = computed(() => {
  return props.selectedRepo
    ? props.events.filter((e) => e.repo_url === props.selectedRepo)
    : props.events;
});

watch(
  () => filtered.value.length,
  async () => {
    if (!stickToBottom.value) return;
    await nextTick();
    if (scrollEl.value) {
      scrollEl.value.scrollTop = scrollEl.value.scrollHeight;
    }
  },
);

function eventEmoji(type: string): string {
  return (
    {
      PreToolUse: "🔧",
      PostToolUse: "✅",
      UserPromptSubmit: "💬",
      Stop: "🛑",
      SubagentStop: "↩️",
      Notification: "🔔",
      PreCompact: "📦",
    }[type] ?? "·"
  );
}

function fmt(ts: number | undefined): string {
  if (!ts) return "";
  return new Date(ts).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function shortRepo(url: string | undefined): string {
  if (!url) return "";
  return url.replace("https://github.com/", "").split("/").slice(-1)[0];
}

function payloadSummary(e: HookEvent): string {
  if (e.summary) return e.summary;
  const toolName = e.payload?.tool_name as string | undefined;
  if (toolName) return toolName;
  const prompt = e.payload?.prompt as string | undefined;
  if (prompt) return prompt.slice(0, 120);
  return "";
}

function onScroll(): void {
  if (!scrollEl.value) return;
  const el = scrollEl.value;
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  stickToBottom.value = atBottom;
}
</script>

<template>
  <section class="flex flex-col h-full border-t border-ink-800">
    <header class="flex items-center justify-between px-4 py-2 border-b border-ink-800">
      <h2 class="text-ink-200 uppercase tracking-wider text-xs">
        Event Stream ({{ filtered.length }})
      </h2>
      <label class="text-[11px] text-ink-400 flex items-center gap-1">
        <input type="checkbox" v-model="stickToBottom" class="accent-accent-running" />
        follow
      </label>
    </header>
    <div
      ref="scrollEl"
      @scroll="onScroll"
      class="flex-1 overflow-auto px-4 py-2 space-y-1"
    >
      <div
        v-for="(e, i) in filtered"
        :key="e.id ?? i"
        class="flex gap-2 text-[11px] hover:bg-ink-800/60 rounded px-1 py-0.5"
      >
        <span class="w-12 text-ink-400 shrink-0">{{ fmt(e.timestamp) }}</span>
        <span class="w-5 shrink-0">{{ eventEmoji(e.hook_event_type) }}</span>
        <span class="w-16 text-accent-running truncate shrink-0">
          {{ e.hook_event_type }}
        </span>
        <span class="w-20 text-ink-400 truncate shrink-0">{{ shortRepo(e.repo_url) }}</span>
        <span class="w-16 text-ink-400 font-mono truncate shrink-0">
          {{ e.adw_id ?? e.session_id.slice(0, 8) }}
        </span>
        <span class="text-ink-100 truncate flex-1">{{ payloadSummary(e) }}</span>
      </div>
      <div v-if="filtered.length === 0" class="text-ink-400 italic text-xs py-4">
        No events yet. Hooks will stream here as agents work.
      </div>
    </div>
  </section>
</template>
