<script setup lang="ts">
/**
 * CommandPalette — Cmd+K overlay modal for operator quick-access.
 *
 * Opens as a fixed overlay at the bottom of the viewport when the user
 * presses Cmd+K (macOS) or Ctrl+K (Linux/Windows). Provides:
 *   - System info: session_id, working_dir from GET /api/system-info
 *   - Slash command badges (clickable — appends to input)
 *   - ADW workflow badges (clickable — appends workflow start command)
 *   - Orchestrator tool badges (clickable — appends to input)
 *   - Agent name badges from store.agents
 *   - Text input for sending messages via POST /api/chat/send
 *   - Quick actions: /status, /dispatch, /interrupt
 *   - Keyboard navigation: Escape to close
 *
 * Visibility is driven by store.commandPaletteVisible (set by T125).
 * Rendered via <Teleport to="body"> so z-index stacks above all panels.
 *
 * Adapted from orchestrator_3_stream/GlobalCommandInput.vue — autocomplete
 * removed (useAutocomplete composable not available in tac-master).
 *
 * @task T128
 * @epic T121
 */

import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { useOrchestratorStore } from '../stores/orchestratorStore'
import { useToastStore } from '../stores/toastStore'
import { useChatStore } from '../stores/chatStore'
import * as chatService from '../services/chatService'
import type { SystemInfo } from '../types'

// ── Stores ───────────────────────────────────────────────────────────────────
const store = useOrchestratorStore()
const toastStore = useToastStore()
const chatStore = useChatStore()

// ── State ────────────────────────────────────────────────────────────────────
const textareaRef = ref<HTMLTextAreaElement | null>(null)
const message = ref('')
const loading = ref(false)
const systemInfo = ref<SystemInfo | null>(null)
const fetchError = ref<string | null>(null)

// Copy/append visual feedback
const copiedItem = ref<string | null>(null)
let copyTimeout: ReturnType<typeof setTimeout> | null = null

// ── Computed ─────────────────────────────────────────────────────────────────
const isVisible = computed(() => store.commandPaletteVisible)
const isConnected = computed(() => store.isConnected)
const agentList = computed(() => store.agents.slice(0, 20)) // cap at 20 for display

const slashCommands = computed(() =>
  systemInfo.value?.slash_commands ?? []
)

const adwWorkflows = computed(() =>
  systemInfo.value?.adw_workflows ?? []
)

const orchestratorTools = computed(() =>
  systemInfo.value?.orchestrator_tools ?? []
)

const hasSystemPanel = computed(() =>
  systemInfo.value !== null ||
  agentList.value.length > 0 ||
  slashCommands.value.length > 0 ||
  adwWorkflows.value.length > 0 ||
  orchestratorTools.value.length > 0
)

// ── System info fetching ─────────────────────────────────────────────────────
async function fetchSystemInfo(): Promise<void> {
  fetchError.value = null
  try {
    const resp = await fetch('/api/system-info')
    if (!resp.ok) {
      fetchError.value = `HTTP ${resp.status}`
      return
    }
    const data: SystemInfo = await resp.json()
    systemInfo.value = data
  } catch (err: any) {
    fetchError.value = err?.message ?? 'Network error'
    console.error('[CommandPalette] Failed to fetch /api/system-info:', err)
  }
}

// ── Keyboard shortcut: Cmd+K / Ctrl+K ────────────────────────────────────────
function handleGlobalKeydown(e: KeyboardEvent): void {
  // Cmd+K (macOS) or Ctrl+K (Linux/Windows)
  if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
    e.preventDefault()
    store.toggleCommandPalette()
    return
  }
  // Escape closes palette when visible
  if (e.key === 'Escape' && store.commandPaletteVisible) {
    e.preventDefault()
    store.hideCommandPalette()
  }
}

onMounted(() => {
  document.addEventListener('keydown', handleGlobalKeydown)
  // Fetch system info on mount so it's ready when palette first opens
  fetchSystemInfo()
})

onUnmounted(() => {
  document.removeEventListener('keydown', handleGlobalKeydown)
  if (copyTimeout) clearTimeout(copyTimeout)
})

// ── Watch palette visibility ─────────────────────────────────────────────────
watch(isVisible, async (visible) => {
  if (visible) {
    // Refresh system info every time palette opens
    await fetchSystemInfo()
    await nextTick()
    textareaRef.value?.focus()
  } else {
    message.value = ''
  }
})

// ── Badge append helper ───────────────────────────────────────────────────────
function appendToInput(text: string, itemId: string): void {
  if (message.value.trim()) {
    message.value += ' '
  }
  message.value += text

  // Visual feedback
  copiedItem.value = itemId
  if (copyTimeout) clearTimeout(copyTimeout)
  copyTimeout = setTimeout(() => {
    copiedItem.value = null
    copyTimeout = null
  }, 1500)

  nextTick(() => textareaRef.value?.focus())
}

// ── Copy to clipboard (session_id, working_dir) ───────────────────────────────
async function copyToClipboard(text: string, itemId: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text)
    copiedItem.value = itemId
    if (copyTimeout) clearTimeout(copyTimeout)
    copyTimeout = setTimeout(() => {
      copiedItem.value = null
    }, 1500)
  } catch (err) {
    console.error('[CommandPalette] clipboard write failed:', err)
  }
}

// ── Quick action buttons ──────────────────────────────────────────────────────
function insertQuickAction(text: string): void {
  message.value = text
  nextTick(() => textareaRef.value?.focus())
}

// ── Send message ──────────────────────────────────────────────────────────────
async function sendMessage(): Promise<void> {
  const raw = message.value.trim()
  if (!raw || loading.value) return

  loading.value = true
  message.value = ''
  store.hideCommandPalette()

  try {
    const agentId =
      chatStore.orchestratorAgentId ||
      store.orchestratorAgentId ||
      'tac-master'

    // Optimistic user message
    chatStore.addMessage({
      id: `user-${Date.now()}`,
      sender: 'user',
      type: 'text',
      content: raw,
      timestamp: new Date().toISOString(),
    })
    chatStore.setTyping(true)

    await chatService.sendMessage(raw, agentId)
    toastStore.success('Message sent to orchestrator')
  } catch (err: any) {
    toastStore.error(`Failed to send: ${err?.message ?? 'Unknown error'}`)
    chatStore.setTyping(false)
  } finally {
    loading.value = false
  }
}

// ── Textarea keydown ─────────────────────────────────────────────────────────
function onKeydown(e: KeyboardEvent): void {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    sendMessage()
    return
  }
  if (e.key === 'Escape') {
    store.hideCommandPalette()
  }
}

// ── Click-outside to close ────────────────────────────────────────────────────
function onBackdropClick(e: MouseEvent): void {
  // The backdrop element itself was clicked (not the panel)
  if ((e.target as HTMLElement).classList.contains('cp__backdrop')) {
    store.hideCommandPalette()
  }
}
</script>

<template>
  <Teleport to="body">
    <Transition name="cp-fade">
      <div
        v-if="isVisible"
        class="cp__backdrop"
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        @mousedown="onBackdropClick"
      >
        <div class="cp__panel">
          <!-- Header row -->
          <div class="cp__header">
            <span class="cp__header-title">Command Palette</span>
            <div class="cp__header-shortcuts">
              <kbd>Enter</kbd> send &nbsp;
              <kbd>Shift+Enter</kbd> newline &nbsp;
              <kbd>Esc</kbd> close
            </div>
          </div>

          <!-- Quick actions row -->
          <div class="cp__quick-actions">
            <span class="cp__section-label">Quick actions:</span>
            <button class="cp__qa-btn" @click="insertQuickAction('/status')">
              /status
            </button>
            <button class="cp__qa-btn" @click="insertQuickAction('/dispatch ')">
              /dispatch N
            </button>
            <button class="cp__qa-btn" @click="insertQuickAction('/interrupt')">
              /interrupt
            </button>
          </div>

          <!-- Input area -->
          <div class="cp__input-wrap">
            <textarea
              ref="textareaRef"
              v-model="message"
              class="cp__textarea"
              placeholder="Type a command or message to the orchestrator…"
              rows="3"
              autocomplete="off"
              spellcheck="false"
              aria-label="Command input"
              :disabled="loading"
              @keydown="onKeydown"
            />
            <button
              class="cp__send-btn"
              :class="{ 'cp__send-btn--loading': loading }"
              :disabled="!message.trim() || loading"
              aria-label="Send"
              @click="sendMessage"
            >
              <span v-if="loading" class="cp__spinner" />
              <span v-else>SEND &#9654;</span>
            </button>
          </div>

          <!-- System info panel -->
          <div v-if="hasSystemPanel" class="cp__info-panel">

            <!-- Connection status -->
            <div class="cp__info-row">
              <span class="cp__info-label">Connection:</span>
              <span
                class="cp__info-value cp__status-dot"
                :class="isConnected ? 'cp__status-dot--ok' : 'cp__status-dot--err'"
              >
                {{ isConnected ? 'connected' : 'disconnected' }}
              </span>
            </div>

            <!-- Session ID -->
            <div v-if="systemInfo?.session_id" class="cp__info-row">
              <span class="cp__info-label">Session ID:</span>
              <span
                class="cp__info-value cp__clickable"
                :class="{ 'cp__copied': copiedItem === 'session_id' }"
                title="Click to copy"
                @click="copyToClipboard(systemInfo.session_id, 'session_id')"
              >
                {{ systemInfo.session_id }}
                <span v-if="copiedItem === 'session_id'" class="cp__copied-label">Copied</span>
              </span>
            </div>

            <!-- Working directory -->
            <div v-if="systemInfo?.working_dir" class="cp__info-row">
              <span class="cp__info-label">Working Dir:</span>
              <span
                class="cp__info-value cp__clickable"
                :class="{ 'cp__copied': copiedItem === 'working_dir' }"
                title="Click to copy"
                @click="copyToClipboard(systemInfo.working_dir, 'working_dir')"
              >
                {{ systemInfo.working_dir }}
                <span v-if="copiedItem === 'working_dir'" class="cp__copied-label">Copied</span>
              </span>
            </div>

            <!-- Agent badges -->
            <div v-if="agentList.length > 0" class="cp__info-row">
              <span class="cp__info-label">Agents:</span>
              <div class="cp__badge-group">
                <button
                  v-for="agent in agentList"
                  :key="agent.id"
                  class="cp__badge cp__badge--agent"
                  :class="{ 'cp__badge--active': copiedItem === `agent-${agent.id}` }"
                  :title="`${agent.name} — click to insert`"
                  @click="appendToInput(agent.name, `agent-${agent.id}`)"
                >
                  {{ agent.name }}
                </button>
              </div>
            </div>

            <!-- Slash commands -->
            <div v-if="slashCommands.length > 0" class="cp__info-row">
              <span class="cp__info-label">Slash Commands:</span>
              <div class="cp__badge-group">
                <button
                  v-for="cmd in slashCommands"
                  :key="cmd.name"
                  class="cp__badge cp__badge--cmd"
                  :class="{ 'cp__badge--active': copiedItem === `cmd-${cmd.name}` }"
                  :title="`${cmd.description} — click to insert`"
                  @click="appendToInput(`/${cmd.name}`, `cmd-${cmd.name}`)"
                >
                  /{{ cmd.name }}
                </button>
              </div>
            </div>

            <!-- ADW workflows -->
            <div v-if="adwWorkflows.length > 0" class="cp__info-row">
              <span class="cp__info-label">ADW Workflows:</span>
              <div class="cp__badge-group">
                <button
                  v-for="wf in adwWorkflows"
                  :key="wf.name"
                  class="cp__badge cp__badge--adw"
                  :class="{ 'cp__badge--active': copiedItem === `adw-${wf.name}` }"
                  :title="`${wf.description} — click to insert`"
                  @click="appendToInput(`start_adw workflow_type=${wf.display_name}`, `adw-${wf.name}`)"
                >
                  {{ wf.display_name }}
                </button>
              </div>
            </div>

            <!-- Orchestrator tools -->
            <div v-if="orchestratorTools.length > 0" class="cp__info-row">
              <span class="cp__info-label">Tools:</span>
              <div class="cp__badge-group">
                <button
                  v-for="(tool, i) in orchestratorTools"
                  :key="i"
                  class="cp__badge cp__badge--tool"
                  :class="{ 'cp__badge--active': copiedItem === `tool-${i}` }"
                  :title="`Click to insert`"
                  @click="appendToInput(tool, `tool-${i}`)"
                >
                  {{ tool }}
                </button>
              </div>
            </div>

            <!-- Fetch error notice -->
            <div v-if="fetchError" class="cp__info-row cp__info-row--error">
              <span class="cp__info-label">System info:</span>
              <span class="cp__info-value cp__error-text">
                Failed to load ({{ fetchError }})
              </span>
            </div>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
/* ── Backdrop ──────────────────────────────────────────────────────────────── */
.cp__backdrop {
  position: fixed;
  inset: 0;
  z-index: 1000;
  /* Subtle dark veil — the panel itself is the main visual focus */
  background: rgba(0, 0, 0, 0.45);
  backdrop-filter: blur(2px);
  display: flex;
  align-items: flex-end;
  justify-content: stretch;
}

/* ── Panel ─────────────────────────────────────────────────────────────────── */
.cp__panel {
  width: 100%;
  background: rgba(0, 0, 0, 0.92);
  backdrop-filter: blur(10px);
  border-top: 1px solid var(--accent-cyan, #00ffcc);
  box-shadow: 0 -6px 32px rgba(0, 0, 0, 0.7);
  display: flex;
  flex-direction: column;
  gap: 0;
  max-height: 70vh;
  overflow-y: auto;
}

/* ── Header row ────────────────────────────────────────────────────────────── */
.cp__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px;
  border-bottom: 1px solid rgba(0, 255, 204, 0.15);
  background: rgba(0, 255, 204, 0.04);
  flex-shrink: 0;
}

.cp__header-title {
  font-family: var(--cc-font, ui-monospace, monospace);
  font-size: 11px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--accent-cyan, #00ffcc);
  opacity: 0.8;
}

.cp__header-shortcuts {
  font-family: var(--cc-font, ui-monospace, monospace);
  font-size: 10px;
  color: rgba(255, 255, 255, 0.35);
}

.cp__header-shortcuts kbd {
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.15);
  border-radius: 3px;
  padding: 1px 5px;
  font-size: 10px;
  color: rgba(255, 255, 255, 0.55);
}

/* ── Quick actions ─────────────────────────────────────────────────────────── */
.cp__quick-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
  flex-shrink: 0;
  flex-wrap: wrap;
}

.cp__section-label {
  font-family: var(--cc-font, ui-monospace, monospace);
  font-size: 10px;
  color: rgba(255, 255, 255, 0.35);
  flex-shrink: 0;
}

.cp__qa-btn {
  font-family: var(--cc-font, ui-monospace, monospace);
  font-size: 11px;
  padding: 3px 10px;
  background: rgba(0, 255, 204, 0.06);
  border: 1px solid rgba(0, 255, 204, 0.25);
  border-radius: 3px;
  color: var(--accent-cyan, #00ffcc);
  cursor: pointer;
  transition: background 0.12s ease, border-color 0.12s ease;
}

.cp__qa-btn:hover {
  background: rgba(0, 255, 204, 0.14);
  border-color: rgba(0, 255, 204, 0.5);
}

/* ── Input area ────────────────────────────────────────────────────────────── */
.cp__input-wrap {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 12px 16px;
  flex-shrink: 0;
}

.cp__textarea {
  flex: 1;
  min-width: 0;
  background: #0d0d0d;
  border: 1px solid rgba(0, 255, 204, 0.3);
  border-radius: 4px;
  color: var(--cc-text, #e0e0e0);
  font-family: var(--cc-font, ui-monospace, monospace);
  font-size: 13px;
  line-height: 1.5;
  padding: 8px 10px;
  resize: none;
  outline: none;
  transition: border-color 0.15s ease;
}

.cp__textarea::placeholder {
  color: rgba(255, 255, 255, 0.25);
}

.cp__textarea:focus {
  border-color: var(--accent-cyan, #00ffcc);
  box-shadow: 0 0 0 1px rgba(0, 255, 204, 0.1);
}

.cp__textarea:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* ── Send button ───────────────────────────────────────────────────────────── */
.cp__send-btn {
  flex-shrink: 0;
  height: 36px;
  padding: 0 16px;
  background: transparent;
  border: 1px solid var(--accent-cyan, #00ffcc);
  border-radius: 4px;
  color: var(--accent-cyan, #00ffcc);
  font-family: var(--cc-font, ui-monospace, monospace);
  font-size: 11px;
  letter-spacing: 0.05em;
  cursor: pointer;
  transition: background 0.12s ease, opacity 0.12s ease;
  display: flex;
  align-items: center;
  gap: 6px;
  align-self: flex-start;
  margin-top: 2px;
}

.cp__send-btn:hover:not(:disabled) {
  background: rgba(0, 255, 204, 0.1);
}

.cp__send-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.cp__send-btn--loading {
  cursor: wait;
}

/* ── Spinner ───────────────────────────────────────────────────────────────── */
.cp__spinner {
  display: inline-block;
  width: 12px;
  height: 12px;
  border: 2px solid rgba(0, 255, 204, 0.25);
  border-top-color: var(--accent-cyan, #00ffcc);
  border-radius: 50%;
  animation: cp-spin 0.7s linear infinite;
}

@keyframes cp-spin {
  to { transform: rotate(360deg); }
}

/* ── Info panel ────────────────────────────────────────────────────────────── */
.cp__info-panel {
  padding: 10px 16px 14px;
  border-top: 1px solid rgba(0, 255, 204, 0.1);
  background: rgba(0, 255, 204, 0.025);
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.cp__info-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  font-size: 12px;
}

.cp__info-row--error {
  opacity: 0.7;
}

.cp__info-label {
  font-family: var(--cc-font, ui-monospace, monospace);
  font-size: 10px;
  color: rgba(255, 255, 255, 0.4);
  min-width: 110px;
  flex-shrink: 0;
  padding-top: 3px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

.cp__info-value {
  font-family: var(--cc-font, ui-monospace, monospace);
  font-size: 11px;
  color: rgba(255, 255, 255, 0.75);
  word-break: break-all;
  position: relative;
}

/* Connection status dot */
.cp__status-dot::before {
  content: '';
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  margin-right: 6px;
  vertical-align: middle;
}

.cp__status-dot--ok {
  color: #4ade80;
}

.cp__status-dot--ok::before {
  background: #4ade80;
  box-shadow: 0 0 6px #4ade80;
}

.cp__status-dot--err {
  color: #f87171;
}

.cp__status-dot--err::before {
  background: #f87171;
}

/* Clickable values (session id, cwd) */
.cp__clickable {
  cursor: pointer;
  padding: 2px 6px;
  border-radius: 3px;
  border-left: 2px solid rgba(0, 255, 204, 0.2);
  transition: background 0.15s ease, border-left-color 0.15s ease;
  padding-left: 8px;
}

.cp__clickable:hover {
  background: rgba(0, 255, 204, 0.08);
  border-left-color: rgba(0, 255, 204, 0.5);
}

.cp__copied {
  background: rgba(74, 222, 128, 0.12);
  border-left-color: #4ade80;
}

.cp__copied-label {
  margin-left: 8px;
  font-size: 10px;
  color: #4ade80;
  font-weight: 500;
}

.cp__error-text {
  color: #f87171;
}

/* ── Badge groups ──────────────────────────────────────────────────────────── */
.cp__badge-group {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  align-items: center;
}

.cp__badge {
  font-family: var(--cc-font, ui-monospace, monospace);
  font-size: 10px;
  padding: 2px 8px;
  border-radius: 3px;
  cursor: pointer;
  border: 1px solid;
  transition: all 0.12s ease;
  background: transparent;
  line-height: 1.5;
}

.cp__badge:hover {
  transform: translateY(-1px);
  filter: brightness(1.2);
}

.cp__badge--active {
  filter: brightness(1.4);
  animation: cp-pulse 0.3s ease;
}

@keyframes cp-pulse {
  0%, 100% { transform: scale(1); }
  50% { transform: scale(1.04); }
}

/* Agent badges */
.cp__badge--agent {
  color: #93c5fd;
  border-color: rgba(147, 197, 253, 0.35);
  background: rgba(147, 197, 253, 0.06);
}

.cp__badge--agent:hover {
  background: rgba(147, 197, 253, 0.14);
  border-color: rgba(147, 197, 253, 0.6);
}

/* Slash command badges */
.cp__badge--cmd {
  color: var(--accent-cyan, #00ffcc);
  border-color: rgba(0, 255, 204, 0.3);
  background: rgba(0, 255, 204, 0.05);
}

.cp__badge--cmd:hover {
  background: rgba(0, 255, 204, 0.12);
  border-color: rgba(0, 255, 204, 0.55);
}

/* ADW workflow badges */
.cp__badge--adw {
  color: #c084fc;
  border-color: rgba(192, 132, 252, 0.3);
  background: rgba(192, 132, 252, 0.06);
}

.cp__badge--adw:hover {
  background: rgba(192, 132, 252, 0.14);
  border-color: rgba(192, 132, 252, 0.55);
}

/* Tool badges */
.cp__badge--tool {
  color: #fbbf24;
  border-color: rgba(251, 191, 36, 0.3);
  background: rgba(251, 191, 36, 0.05);
}

.cp__badge--tool:hover {
  background: rgba(251, 191, 36, 0.12);
  border-color: rgba(251, 191, 36, 0.55);
}

/* ── Transition ────────────────────────────────────────────────────────────── */
.cp-fade-enter-active,
.cp-fade-leave-active {
  transition: opacity 0.15s ease;
}

.cp-fade-enter-active .cp__panel,
.cp-fade-leave-active .cp__panel {
  transition: transform 0.15s ease;
}

.cp-fade-enter-from,
.cp-fade-leave-to {
  opacity: 0;
}

.cp-fade-enter-from .cp__panel,
.cp-fade-leave-to .cp__panel {
  transform: translateY(12px);
}
</style>
