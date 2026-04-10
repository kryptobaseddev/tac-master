/**
 * toastStore — lightweight Pinia store for operator toast notifications.
 *
 * Usage:
 *   const toast = useToastStore()
 *   toast.success('Done!')
 *   toast.error('Boom!')
 *
 * Toasts auto-dismiss after 4 s.  The <Toast /> component renders the stack.
 *
 * @task T053
 * @epic T051
 */

import { defineStore } from 'pinia'
import { ref } from 'vue'

export type ToastType = 'success' | 'error'

export interface ToastItem {
  id: number
  type: ToastType
  message: string
}

const AUTO_DISMISS_MS = 4_000

let _seq = 0

export const useToastStore = defineStore('toast', () => {
  const toasts = ref<ToastItem[]>([])

  function push(type: ToastType, message: string): number {
    const id = ++_seq
    toasts.value.push({ id, type, message })
    setTimeout(() => dismiss(id), AUTO_DISMISS_MS)
    return id
  }

  function success(message: string) {
    return push('success', message)
  }

  function error(message: string) {
    return push('error', message)
  }

  function dismiss(id: number) {
    const idx = toasts.value.findIndex(t => t.id === id)
    if (idx !== -1) toasts.value.splice(idx, 1)
  }

  return { toasts, success, error, dismiss }
})
