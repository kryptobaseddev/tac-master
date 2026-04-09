<script setup lang="ts">
/**
 * SystemLogs — displays the last N lines of the tac-master daemon log.
 *
 * Fetches from GET /api/logs/daemon (server reads journalctl or daemon.stdout.log).
 * Auto-refreshes every 15 seconds. Renders lines in a monospace pre block.
 *
 * @task T043
 * @epic T042
 */

import { ref, onMounted, onUnmounted } from "vue";

interface LogResponse {
  lines: string[];
  source: string;
  error?: string;
}

const lines = ref<string[]>([]);
const source = ref<string>("");
const loading = ref(false);
const error = ref<string | null>(null);

async function fetchLogs(): Promise<void> {
  loading.value = true;
  try {
    const resp = await fetch("/api/logs/daemon");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = (await resp.json()) as LogResponse;
    if (data.error) {
      error.value = data.error;
    } else {
      lines.value = data.lines ?? [];
      source.value = data.source ?? "";
      error.value = null;
    }
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
}

let _timer: ReturnType<typeof setInterval> | null = null;

onMounted(() => {
  fetchLogs();
  _timer = setInterval(fetchLogs, 15_000);
});

onUnmounted(() => {
  if (_timer !== null) clearInterval(_timer);
});
</script>

<template>
  <div class="sl">
    <!-- ── Header ──────────────────────────────────────────── -->
    <div class="sl__header">
      <span class="sl__title">SYSTEM_LOGS</span>
      <span v-if="source" class="sl__source">{{ source }}</span>
      <button
        class="sl__refresh"
        :class="{ 'sl__refresh--spinning': loading }"
        title="Refresh logs"
        @click="fetchLogs"
      >
        ⟳
      </button>
    </div>

    <!-- ── Error ───────────────────────────────────────────── -->
    <div v-if="error" class="sl__error">
      <span>ERROR: {{ error }}</span>
    </div>

    <!-- ── Log output ─────────────────────────────────────── -->
    <div v-else class="sl__wrap">
      <pre class="sl__pre"><template v-if="lines.length === 0 && !loading">— no log output —</template><template v-else>{{ lines.join("\n") }}</template></pre>
    </div>
  </div>
</template>

<style scoped>
.sl {
  display: flex;
  flex-direction: column;
  height: 100%;
  font-family: var(--cc-font, ui-monospace, monospace);
  background: var(--cc-bg, #0a0a0a);
  color: var(--cc-text, #e0e0e0);
  overflow: hidden;
}

/* ── Header ─────────────────────────────────────────────────── */
.sl__header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px 8px;
  border-bottom: 1px solid var(--cc-border, #1a1a1a);
  flex-shrink: 0;
}

.sl__title {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.12em;
  color: var(--cc-cyan, #00ffcc);
  text-shadow: 0 0 8px rgba(0, 255, 204, 0.3);
}

.sl__source {
  font-size: 9px;
  color: var(--cc-text-muted, #666);
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sl__refresh {
  background: transparent;
  border: 1px solid var(--cc-border-mid, #222);
  color: var(--cc-text-muted, #666);
  font-size: 12px;
  width: 22px;
  height: 22px;
  border-radius: 3px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s ease;
  flex-shrink: 0;
}

.sl__refresh:hover {
  color: var(--cc-cyan, #00ffcc);
  border-color: var(--cc-cyan, #00ffcc);
}

.sl__refresh--spinning {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to   { transform: rotate(360deg); }
}

/* ── Error ───────────────────────────────────────────────────── */
.sl__error {
  padding: 12px 16px;
  font-size: 10px;
  color: var(--cc-red, #ff4466);
}

/* ── Log area ────────────────────────────────────────────────── */
.sl__wrap {
  flex: 1;
  overflow: auto;
  padding: 8px 0;
  scrollbar-width: thin;
  scrollbar-color: var(--cc-border-mid, #222) var(--cc-bg, #0a0a0a);
}

.sl__wrap::-webkit-scrollbar { width: 4px; height: 4px; }
.sl__wrap::-webkit-scrollbar-track { background: var(--cc-bg, #0a0a0a); }
.sl__wrap::-webkit-scrollbar-thumb { background: var(--cc-border-mid, #222); border-radius: 2px; }

.sl__pre {
  margin: 0;
  padding: 0 16px;
  font-family: var(--cc-font, ui-monospace, monospace);
  font-size: 10px;
  line-height: 1.6;
  color: var(--cc-text, #e0e0e0);
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
