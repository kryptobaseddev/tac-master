<template>
  <div class="oplog" :class="{ 'oplog--collapsed': collapsed }">
    <!-- Header -->
    <div class="oplog__header" @click="collapsed = !collapsed">
      <span class="oplog__title">OPERATOR LOG</span>
      <span class="oplog__badge" v-if="entries.length">{{ entries.length }}</span>
      <span class="oplog__chevron">{{ collapsed ? '▸' : '▾' }}</span>
    </div>

    <!-- Body -->
    <div v-if="!collapsed" class="oplog__body">
      <!-- Empty state -->
      <div v-if="entries.length === 0" class="oplog__empty">No activity yet.</div>

      <!-- Log entries -->
      <div class="oplog__feed" ref="feedEl">
        <div
          v-for="entry in entries"
          :key="entry.id"
          class="oplog__entry"
          :class="`oplog__entry--${entry.action === 'error' ? 'error' : entry.source}`"
        >
          <span class="oplog__ts">{{ formatTs(entry.timestamp) }}</span>
          <span class="oplog__icon" :title="entry.source">{{ sourceIcon(entry) }}</span>
          <span class="oplog__msg">
            {{ entry.message }}
            <span
              v-if="entry.task_id"
              class="oplog__link"
              @click.stop="openTask(entry.task_id)"
              :title="`Open task ${entry.task_id}`"
            >{{ entry.task_id }}</span>
            <a
              v-if="entry.issue_number"
              class="oplog__ghlink"
              :href="`https://github.com/kryptobaseddev/tac-master/issues/${entry.issue_number}`"
              target="_blank"
              rel="noopener noreferrer"
              :title="`View GitHub issue #${entry.issue_number}`"
            >#{{ entry.issue_number }}</a>
          </span>
        </div>
      </div>

      <!-- Inline command input -->
      <div class="oplog__input-row">
        <input
          class="oplog__input"
          v-model="cmdText"
          placeholder="Quick command..."
          @keydown.enter="sendCommand"
          :disabled="cmdBusy"
          maxlength="200"
        />
        <button class="oplog__send" @click="sendCommand" :disabled="cmdBusy || !cmdText.trim()">
          &#9658;
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, nextTick, watch } from "vue";
import { useCleoStore } from "../stores/cleoStore";

interface LogEntry {
  id: number;
  timestamp: number;
  source: string;
  action: string;
  message: string;
  task_id: string | null;
  issue_number: number | null;
  adw_id: string | null;
  metadata: any;
}

const cleoStore = useCleoStore();

const entries = ref<LogEntry[]>([]);
const collapsed = ref(false);
const feedEl = ref<HTMLElement | null>(null);
const cmdText = ref("");
const cmdBusy = ref(false);

let _pollTimer: ReturnType<typeof setInterval> | null = null;

async function fetchLog(): Promise<void> {
  try {
    const resp = await fetch("/api/operator-log?limit=50");
    if (!resp.ok) return;
    const data = (await resp.json()) as LogEntry[];
    entries.value = data;
    await nextTick();
    scrollToBottom();
  } catch {
    // silently ignore fetch errors
  }
}

function scrollToBottom(): void {
  if (feedEl.value) {
    feedEl.value.scrollTop = feedEl.value.scrollHeight;
  }
}

function formatTs(ts: number): string {
  // ts is seconds since epoch
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function sourceIcon(entry: LogEntry): string {
  if (entry.action === "error") return "X";
  switch (entry.source) {
    case "operator": return ">";
    case "daemon":   return "D";
    case "system":   return "*";
    default:         return "?";
  }
}

function openTask(taskId: string | null): void {
  if (!taskId) return;
  cleoStore.openTaskModal(taskId);
}

async function sendCommand(): Promise<void> {
  const text = cmdText.value.trim();
  if (!text || cmdBusy.value) return;
  cmdBusy.value = true;
  cmdText.value = "";
  try {
    await fetch("/api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type: "freetext", text }),
    });
    // Immediately refresh log
    await fetchLog();
  } catch {
    // ignore
  } finally {
    cmdBusy.value = false;
  }
}

watch(collapsed, async (val) => {
  if (!val) {
    await nextTick();
    scrollToBottom();
  }
});

onMounted(() => {
  fetchLog();
  _pollTimer = setInterval(fetchLog, 5000);
});

onUnmounted(() => {
  if (_pollTimer !== null) clearInterval(_pollTimer);
});
</script>

<style scoped>
.oplog {
  display: flex;
  flex-direction: column;
  background: #0a0d12;
  border-top: 1px solid #1a2030;
  font-family: ui-monospace, "Cascadia Code", Menlo, Consolas, monospace;
  font-size: 11px;
  flex-shrink: 0;
  min-height: 0;
  max-height: 240px;
  transition: max-height 0.2s ease;
}

.oplog--collapsed {
  max-height: 28px;
}

.oplog__header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 10px;
  cursor: pointer;
  user-select: none;
  flex-shrink: 0;
  border-bottom: 1px solid #1a2030;
  background: #0d1117;
}

.oplog--collapsed .oplog__header {
  border-bottom: none;
}

.oplog__title {
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.14em;
  color: #484f58;
  text-transform: uppercase;
  flex: 1;
}

.oplog__badge {
  font-size: 9px;
  background: #1e2128;
  color: #8b949e;
  border-radius: 8px;
  padding: 0 5px;
  line-height: 14px;
}

.oplog__chevron {
  font-size: 9px;
  color: #484f58;
}

.oplog__body {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.oplog__empty {
  padding: 10px;
  color: #484f58;
  text-align: center;
  font-size: 10px;
}

.oplog__feed {
  flex: 1;
  overflow-y: auto;
  padding: 4px 0;
  min-height: 0;
}

.oplog__feed::-webkit-scrollbar { width: 3px; }
.oplog__feed::-webkit-scrollbar-track { background: transparent; }
.oplog__feed::-webkit-scrollbar-thumb { background: #1a2030; border-radius: 2px; }

.oplog__entry {
  display: flex;
  align-items: baseline;
  gap: 5px;
  padding: 2px 10px;
  line-height: 1.5;
  border-bottom: 1px solid rgba(255,255,255,0.03);
}

.oplog__entry:last-child { border-bottom: none; }

.oplog__entry--error   { background: rgba(247,129,102,0.05); }
.oplog__entry--daemon  { opacity: 0.85; }
.oplog__entry--system  { opacity: 0.9; }
.oplog__entry--operator { }

.oplog__ts {
  font-size: 10px;
  color: #30363d;
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
  min-width: 58px;
}

.oplog__icon {
  flex-shrink: 0;
  font-size: 10px;
  width: 12px;
  text-align: center;
  font-weight: 700;
}

.oplog__entry--error   .oplog__icon { color: #f78166; }
.oplog__entry--operator .oplog__icon { color: #39d0d8; }
.oplog__entry--system  .oplog__icon { color: #58a6ff; }
.oplog__entry--daemon  .oplog__icon { color: #3fb950; }

.oplog__msg {
  flex: 1;
  color: #8b949e;
  font-size: 10px;
  line-height: 1.5;
  word-break: break-word;
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 3px;
}

.oplog__entry--error .oplog__msg { color: #f78166; }

.oplog__link {
  color: #58a6ff;
  cursor: pointer;
  font-size: 10px;
  border-bottom: 1px dotted #1a3050;
  padding: 0 1px;
}
.oplog__link:hover { color: #79c0ff; }

.oplog__ghlink {
  color: #3fb950;
  text-decoration: none;
  font-size: 10px;
  border-bottom: 1px dotted #1a4020;
  padding: 0 1px;
}
.oplog__ghlink:hover { color: #56d364; }

.oplog__input-row {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  border-top: 1px solid #1a2030;
  flex-shrink: 0;
}

.oplog__input {
  flex: 1;
  background: #0d1117;
  color: #c9d1d9;
  border: 1px solid #21262d;
  border-radius: 3px;
  padding: 3px 7px;
  font-family: ui-monospace, "Cascadia Code", Menlo, Consolas, monospace;
  font-size: 10px;
  outline: none;
  transition: border-color 0.15s;
}
.oplog__input:focus { border-color: #39d0d8; }
.oplog__input:disabled { opacity: 0.5; }
.oplog__input::placeholder { color: #30363d; }

.oplog__send {
  flex-shrink: 0;
  background: none;
  border: 1px solid #21262d;
  border-radius: 3px;
  color: #484f58;
  font-size: 10px;
  padding: 3px 6px;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
}
.oplog__send:hover:not(:disabled) { border-color: #39d0d8; color: #39d0d8; }
.oplog__send:disabled { opacity: 0.45; cursor: not-allowed; }
</style>
