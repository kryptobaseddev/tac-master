<script setup lang="ts">
/**
 * CommandBar — operator input bar for sending instructions to the agent system.
 *
 * Positioned above the StatusBar (injected via CommandCenterLayout's
 * commandbar slot).  Parses shortcut prefixes before dispatch:
 *
 *   /status            → GET  /api/command (type: status)
 *   /dispatch N        → POST /api/command (type: dispatch, issue: N)
 *   /queue TXXX        → POST /api/command (type: queue,    taskId: TXXX)
 *   /note TXXX message → POST /api/command (type: note,     taskId, message)
 *   <free text>        → POST /api/command (type: freetext) → creates GH issue
 *
 * Command history (last 20) is persisted to localStorage.
 * Autocomplete dropdown appears when input starts with "/".
 *
 * @task T053
 * @epic T051
 */

import { ref, computed, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { useToastStore } from '../stores/toastStore'

// ── Constants ────────────────────────────────────────────────────────────────
const HISTORY_KEY   = 'cmd-bar-history'
const MAX_HISTORY   = 20

const SHORTCUTS = [
  { cmd: '/status',        desc: 'Show daemon health' },
  { cmd: '/dispatch N',    desc: 'Force re-dispatch issue N' },
  { cmd: '/queue TXXX',    desc: 'Queue a CLEO task for work' },
  { cmd: '/note TXXX msg', desc: 'Add note to CLEO task' },
]

// ── Store ────────────────────────────────────────────────────────────────────
const toastStore = useToastStore()

// ── State ────────────────────────────────────────────────────────────────────
const input       = ref('')
const loading     = ref(false)
const showDropdown = ref(false)
const historyIdx  = ref(-1)          // -1 = editing current line

const history = ref<string[]>([])    // newest-first

const inputEl = ref<HTMLInputElement | null>(null)

// ── Autocomplete ─────────────────────────────────────────────────────────────
const filteredShortcuts = computed(() => {
  if (!input.value.startsWith('/')) return []
  const q = input.value.toLowerCase()
  return SHORTCUTS.filter(s => s.cmd.toLowerCase().startsWith(q))
})

// ── localStorage helpers ──────────────────────────────────────────────────────
function loadHistory(): string[] {
  try {
    const raw = localStorage.getItem(HISTORY_KEY)
    if (!raw) return []
    return JSON.parse(raw)
  } catch {
    return []
  }
}

function saveHistory(h: string[]) {
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(h))
  } catch {
    // storage full or unavailable
  }
}

function pushHistory(cmd: string) {
  if (!cmd.trim()) return
  const next = [cmd, ...history.value.filter(h => h !== cmd)].slice(0, MAX_HISTORY)
  history.value = next
  saveHistory(next)
}

onMounted(() => {
  history.value = loadHistory()
})

// ── Input handling ───────────────────────────────────────────────────────────
function onInput() {
  historyIdx.value = -1
  showDropdown.value = input.value.startsWith('/')
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'ArrowUp') {
    e.preventDefault()
    if (history.value.length === 0) return
    if (historyIdx.value < history.value.length - 1) {
      historyIdx.value++
      input.value = history.value[historyIdx.value]
    }
    showDropdown.value = false
    return
  }

  if (e.key === 'ArrowDown') {
    e.preventDefault()
    if (historyIdx.value > 0) {
      historyIdx.value--
      input.value = history.value[historyIdx.value]
    } else if (historyIdx.value === 0) {
      historyIdx.value = -1
      input.value = ''
    }
    showDropdown.value = false
    return
  }

  if (e.key === 'Escape') {
    showDropdown.value = false
    return
  }

  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    submitCommand()
  }
}

function selectShortcut(cmd: string) {
  // Replace template tokens with cursor-friendly placeholder
  input.value = cmd
  showDropdown.value = false
  nextTick(() => inputEl.value?.focus())
}

// ── Command parsing & dispatch ────────────────────────────────────────────────
async function submitCommand() {
  const raw = input.value.trim()
  if (!raw || loading.value) return

  pushHistory(raw)
  input.value = ''
  showDropdown.value = false
  historyIdx.value = -1
  loading.value = true

  try {
    await dispatch(raw)
  } finally {
    loading.value = false
  }
}

async function dispatch(raw: string) {
  // /status
  if (/^\/status\s*$/i.test(raw)) {
    const res = await apiPost({ type: 'status' })
    if (res.ok) {
      const d = res.data
      toastStore.success(`Daemon: ${d?.data?.status ?? 'ok'} | clients: ${d?.data?.clients ?? '?'}`)
    } else {
      toastStore.error(`/status failed: ${res.error}`)
    }
    return
  }

  // /dispatch N
  const dispatchM = raw.match(/^\/dispatch\s+(\d+)\s*$/i)
  if (dispatchM) {
    const issueNum = parseInt(dispatchM[1], 10)
    const res = await apiPost({ type: 'dispatch', issue: issueNum })
    if (res.ok) {
      toastStore.success(`Issue #${issueNum} queued for re-dispatch`)
    } else {
      toastStore.error(`/dispatch failed: ${res.error}`)
    }
    return
  }

  // /queue TXXX
  const queueM = raw.match(/^\/queue\s+(T\w+)\s*$/i)
  if (queueM) {
    const taskId = queueM[1].toUpperCase()
    const res = await apiPost({ type: 'queue', taskId })
    if (res.ok) {
      toastStore.success(`Task ${taskId} queued for work`)
    } else {
      toastStore.error(`/queue failed: ${res.error}`)
    }
    return
  }

  // /note TXXX message
  const noteM = raw.match(/^\/note\s+(T\w+)\s+(.+)$/i)
  if (noteM) {
    const taskId  = noteM[1].toUpperCase()
    const message = noteM[2].trim()
    const res = await apiPost({ type: 'note', taskId, message })
    if (res.ok) {
      toastStore.success(`Note added to ${taskId}`)
    } else {
      toastStore.error(`/note failed: ${res.error}`)
    }
    return
  }

  // unknown slash command
  if (raw.startsWith('/')) {
    toastStore.error(`Unknown command: ${raw.split(' ')[0]}`)
    return
  }

  // Free text → create GitHub issue
  const res = await apiPost({ type: 'freetext', text: raw })
  if (res.ok) {
    const url = res.data?.issue_url
    toastStore.success(url ? `Issue created: ${url}` : 'Command dispatched to agent')
  } else {
    toastStore.error(`Dispatch failed: ${res.error}`)
  }
}

// ── API helper ────────────────────────────────────────────────────────────────
async function apiPost(body: Record<string, unknown>): Promise<{ ok: boolean; data?: any; error?: string }> {
  try {
    const res = await fetch('/api/command', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const data = await res.json()
    if (!res.ok) {
      return { ok: false, error: data?.error ?? `HTTP ${res.status}` }
    }
    return { ok: true, data }
  } catch (e: any) {
    return { ok: false, error: e?.message ?? 'Network error' }
  }
}

// ── Click-outside to close dropdown ──────────────────────────────────────────
function onClickOutside(e: MouseEvent) {
  const el = (e.target as HTMLElement)
  if (!el.closest('.cb')) showDropdown.value = false
}

onMounted(() => document.addEventListener('mousedown', onClickOutside))
onBeforeUnmount(() => document.removeEventListener('mousedown', onClickOutside))
</script>

<template>
  <div class="cb" role="search" aria-label="Operator command bar">
    <!-- Autocomplete dropdown -->
    <Transition name="cb-drop">
      <ul
        v-if="showDropdown && filteredShortcuts.length"
        class="cb__dropdown"
        role="listbox"
        aria-label="Command shortcuts"
      >
        <li
          v-for="s in filteredShortcuts"
          :key="s.cmd"
          class="cb__option"
          role="option"
          @mousedown.prevent="selectShortcut(s.cmd)"
        >
          <span class="cb__option-cmd">{{ s.cmd }}</span>
          <span class="cb__option-desc">{{ s.desc }}</span>
        </li>
      </ul>
    </Transition>

    <!-- Input row -->
    <div class="cb__row">
      <span class="cb__prompt" aria-hidden="true">&gt;</span>
      <input
        ref="inputEl"
        v-model="input"
        class="cb__input"
        type="text"
        placeholder="Type a command or message to the agent…"
        autocomplete="off"
        spellcheck="false"
        aria-label="Command input"
        :disabled="loading"
        @input="onInput"
        @keydown="onKeydown"
        @focus="showDropdown = input.startsWith('/')"
      />
      <button
        class="cb__send"
        :class="{ 'cb__send--loading': loading }"
        :disabled="!input.trim() || loading"
        aria-label="Send command"
        @click="submitCommand"
      >
        <span v-if="loading" class="cb__spinner" aria-hidden="true" />
        <span v-else>SEND &#9654;</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
/* ── Container ───────────────────────────────────────────────────────────── */
.cb {
  position: relative;
  width: 100%;
  background: var(--cc-surface, #111);
  border-top: 1px solid var(--cc-border, #1a1a1a);
  padding: 0 8px;
  box-sizing: border-box;
  height: 38px;
  display: flex;
  align-items: center;
}

/* ── Input row ───────────────────────────────────────────────────────────── */
.cb__row {
  display: flex;
  align-items: center;
  width: 100%;
  gap: 6px;
}

.cb__prompt {
  color: var(--accent-cyan, #00ffcc);
  font-family: var(--cc-font, ui-monospace, monospace);
  font-size: 13px;
  flex-shrink: 0;
  user-select: none;
}

.cb__input {
  flex: 1;
  min-width: 0;
  background: #0d0d0d;
  border: 1px solid transparent;
  border-radius: 3px;
  color: var(--cc-text, #e0e0e0);
  font-family: var(--cc-font, ui-monospace, monospace);
  font-size: 12px;
  padding: 4px 8px;
  outline: none;
  height: 26px;
  box-sizing: border-box;
  transition: border-color 0.15s ease;
}

.cb__input::placeholder {
  color: var(--cc-text-dim, #444);
}

.cb__input:focus {
  border-color: var(--accent-cyan, #00ffcc);
  box-shadow: 0 0 0 1px rgba(0, 255, 204, 0.15);
}

.cb__input:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* ── Send button ─────────────────────────────────────────────────────────── */
.cb__send {
  flex-shrink: 0;
  background: transparent;
  border: 1px solid var(--accent-cyan, #00ffcc);
  border-radius: 3px;
  color: var(--accent-cyan, #00ffcc);
  font-family: var(--cc-font, ui-monospace, monospace);
  font-size: 10px;
  letter-spacing: 0.05em;
  padding: 3px 10px;
  height: 26px;
  cursor: pointer;
  transition: background 0.15s ease, opacity 0.15s ease;
  white-space: nowrap;
}

.cb__send:hover:not(:disabled) {
  background: rgba(0, 255, 204, 0.12);
}

.cb__send:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.cb__send--loading {
  display: flex;
  align-items: center;
  justify-content: center;
}

/* ── Spinner ─────────────────────────────────────────────────────────────── */
.cb__spinner {
  display: inline-block;
  width: 10px;
  height: 10px;
  border: 2px solid rgba(0, 255, 204, 0.3);
  border-top-color: var(--accent-cyan, #00ffcc);
  border-radius: 50%;
  animation: cb-spin 0.7s linear infinite;
}

@keyframes cb-spin {
  to { transform: rotate(360deg); }
}

/* ── Dropdown ────────────────────────────────────────────────────────────── */
.cb__dropdown {
  position: absolute;
  bottom: 100%;
  left: 0;
  right: 0;
  margin: 0 0 2px;
  padding: 0;
  list-style: none;
  background: #141414;
  border: 1px solid var(--accent-cyan, #00ffcc);
  border-radius: 4px 4px 0 0;
  box-shadow: 0 -4px 16px rgba(0, 0, 0, 0.6);
  z-index: 100;
  max-height: 180px;
  overflow-y: auto;
}

.cb__option {
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding: 6px 12px;
  cursor: pointer;
  transition: background 0.1s ease;
}

.cb__option:hover {
  background: rgba(0, 255, 204, 0.08);
}

.cb__option-cmd {
  font-family: var(--cc-font, ui-monospace, monospace);
  font-size: 11px;
  color: var(--accent-cyan, #00ffcc);
  white-space: nowrap;
  flex-shrink: 0;
}

.cb__option-desc {
  font-family: var(--cc-font, ui-monospace, monospace);
  font-size: 10px;
  color: var(--cc-text-muted, #666);
}

/* ── Transition ──────────────────────────────────────────────────────────── */
.cb-drop-enter-active,
.cb-drop-leave-active {
  transition: opacity 0.12s ease, transform 0.12s ease;
}

.cb-drop-enter-from,
.cb-drop-leave-to {
  opacity: 0;
  transform: translateY(4px);
}
</style>
