<script setup lang="ts">
/**
 * Toast — stacked notification overlay.
 *
 * Reads from toastStore and renders at top-right.
 * Each toast auto-dismisses after 4 s; clicking dismisses immediately.
 *
 * @task T053
 * @epic T051
 */

import { useToastStore } from '../stores/toastStore'

const store = useToastStore()
</script>

<template>
  <Teleport to="body">
    <TransitionGroup
      tag="div"
      class="toast-stack"
      name="toast"
      aria-live="polite"
      aria-label="Notifications"
    >
      <div
        v-for="t in store.toasts"
        :key="t.id"
        class="toast"
        :class="`toast--${t.type}`"
        role="alert"
        @click="store.dismiss(t.id)"
      >
        <span class="toast__icon" aria-hidden="true">
          {{ t.type === 'success' ? '✓' : '✗' }}
        </span>
        <span class="toast__msg">{{ t.message }}</span>
        <button class="toast__close" aria-label="Dismiss" @click.stop="store.dismiss(t.id)">
          &times;
        </button>
      </div>
    </TransitionGroup>
  </Teleport>
</template>

<style scoped>
/* ── Stack container ─────────────────────────────────────────────────────── */
.toast-stack {
  position: fixed;
  top: 16px;
  right: 16px;
  z-index: 9999;
  display: flex;
  flex-direction: column;
  gap: 8px;
  pointer-events: none;
  max-width: 380px;
  width: calc(100vw - 32px);
}

/* ── Individual toast ────────────────────────────────────────────────────── */
.toast {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 10px 12px;
  border-radius: 4px;
  background: #141414;
  font-family: var(--cc-font, ui-monospace, monospace);
  font-size: 11px;
  line-height: 1.4;
  color: var(--cc-text, #e0e0e0);
  cursor: pointer;
  pointer-events: all;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.7);
  border-left: 3px solid transparent;
  word-break: break-word;
}

.toast--success {
  border-color: var(--cc-green, #00ff66);
}

.toast--error {
  border-color: #ff4444;
}

.toast__icon {
  flex-shrink: 0;
  font-size: 11px;
  margin-top: 1px;
}

.toast--success .toast__icon {
  color: var(--cc-green, #00ff66);
}

.toast--error .toast__icon {
  color: #ff4444;
}

.toast__msg {
  flex: 1;
  min-width: 0;
}

.toast__close {
  flex-shrink: 0;
  background: none;
  border: none;
  color: var(--cc-text-muted, #666);
  cursor: pointer;
  font-size: 14px;
  line-height: 1;
  padding: 0 0 0 4px;
  margin-top: -1px;
  transition: color 0.1s ease;
}

.toast__close:hover {
  color: var(--cc-text, #e0e0e0);
}

/* ── Transition ──────────────────────────────────────────────────────────── */
.toast-enter-active,
.toast-leave-active {
  transition: opacity 0.2s ease, transform 0.2s ease;
}

.toast-enter-from {
  opacity: 0;
  transform: translateX(24px);
}

.toast-leave-to {
  opacity: 0;
  transform: translateX(24px);
}
</style>
