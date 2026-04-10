<script setup lang="ts">
/**
 * CommandCenterLayout — master CSS Grid shell for the Command Center redesign.
 *
 * Provides five named areas:
 *   header     — full-width top bar (HeaderBar)
 *   sidebar    — left navigation column (RepoSidebar + EpicTaskTree)
 *   main       — scrollable center content area (panels)
 *   right      — right sidebar (LiveExecutionPanel)
 *   statusbar  — full-width bottom KPI bar (StatusBar)
 *
 * Usage:
 *   <CommandCenterLayout>
 *     <template #header>   <HeaderBar  /> </template>
 *     <template #sidebar>  <RepoSidebar /> <EpicTaskTree /> </template>
 *     <template #main>     <ActiveAgentsPanel /> ... </template>
 *     <template #right>    <LiveExecutionPanel /> </template>
 *     <template #statusbar><StatusBar  /> </template>
 *   </CommandCenterLayout>
 *
 * @task T037 (updated T041, T047 drag-resize splitters)
 * @epic T036
 */

import { ref, computed, onMounted, onBeforeUnmount } from 'vue'

// ── Constants ────────────────────────────────────────────────────────────────
const STORAGE_KEY = 'cc-panel-widths'
const DEFAULT_SIDEBAR_W = 280
const DEFAULT_RIGHT_W   = 350
const MIN_SIDEBAR_W     = 200
const MIN_CENTER_W      = 400
const MIN_RIGHT_W       = 250

// ── Reactive state ───────────────────────────────────────────────────────────
const sidebarW = ref(DEFAULT_SIDEBAR_W)
const rightW   = ref(DEFAULT_RIGHT_W)

// ── Computed style for the body flex container ───────────────────────────────
// We drive widths via CSS custom properties on the child elements.
// The body itself remains display:flex — only sidebar/right get explicit widths.

// ── localStorage persistence ─────────────────────────────────────────────────
function saveWidths() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([sidebarW.value, rightW.value]))
  } catch {
    // storage unavailable — silently ignore
  }
}

function loadWidths() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return
    const parsed = JSON.parse(raw)
    if (Array.isArray(parsed) && parsed.length === 2) {
      const [sw, rw] = parsed
      if (typeof sw === 'number' && sw >= MIN_SIDEBAR_W) sidebarW.value = sw
      if (typeof rw === 'number' && rw >= MIN_RIGHT_W)   rightW.value   = rw
    }
  } catch {
    // ignore malformed data
  }
}

onMounted(loadWidths)

// ── Drag state ───────────────────────────────────────────────────────────────
type HandleSide = 'left' | 'right'

let dragSide: HandleSide | null = null
let dragStartX    = 0
let dragStartSideW = 0

const bodyEl = ref<HTMLElement | null>(null)

function getBodyWidth(): number {
  return bodyEl.value?.offsetWidth ?? window.innerWidth
}

function clampSidebar(w: number): number {
  const bodyW = getBodyWidth()
  const maxSidebar = bodyW - rightW.value - MIN_CENTER_W - 8 // 8px handles
  return Math.max(MIN_SIDEBAR_W, Math.min(w, maxSidebar))
}

function clampRight(w: number): number {
  const bodyW = getBodyWidth()
  const maxRight = bodyW - sidebarW.value - MIN_CENTER_W - 8
  return Math.max(MIN_RIGHT_W, Math.min(w, maxRight))
}

function onMouseMove(e: MouseEvent) {
  if (!dragSide) return
  const delta = e.clientX - dragStartX
  if (dragSide === 'left') {
    sidebarW.value = clampSidebar(dragStartSideW + delta)
  } else {
    // Right handle: dragging left increases right panel
    rightW.value = clampRight(dragStartSideW - delta)
  }
}

function onMouseUp() {
  if (!dragSide) return
  dragSide = null
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
  saveWidths()
  window.removeEventListener('mousemove', onMouseMove)
  window.removeEventListener('mouseup', onMouseUp)
}

function startResize(side: HandleSide, e: MouseEvent) {
  e.preventDefault()
  dragSide = side
  dragStartX = e.clientX
  dragStartSideW = side === 'left' ? sidebarW.value : rightW.value
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
  window.addEventListener('mousemove', onMouseMove)
  window.addEventListener('mouseup', onMouseUp)
}

// Clean up listeners if component is destroyed mid-drag
onBeforeUnmount(() => {
  window.removeEventListener('mousemove', onMouseMove)
  window.removeEventListener('mouseup', onMouseUp)
})
</script>

<template>
  <div class="cc-layout">
    <!-- Header area -->
    <header class="cc-layout__header">
      <slot name="header" />
    </header>

    <!-- Body row: sidebar + handles + main + right -->
    <div class="cc-layout__body" ref="bodyEl">
      <aside
        class="cc-layout__sidebar"
        :style="{ width: sidebarW + 'px' }"
      >
        <slot name="sidebar" />
      </aside>

      <!-- Left resize handle (between sidebar and main) -->
      <div
        class="cc-resize-handle"
        @mousedown="startResize('left', $event)"
        title="Drag to resize"
      ></div>

      <main class="cc-layout__main">
        <slot name="main" />
      </main>

      <!-- Right resize handle (between main and right panel) -->
      <div
        v-if="$slots.right"
        class="cc-resize-handle"
        @mousedown="startResize('right', $event)"
        title="Drag to resize"
      ></div>

      <aside
        class="cc-layout__right"
        v-if="$slots.right"
        :style="{ width: rightW + 'px' }"
      >
        <slot name="right" />
      </aside>
    </div>

    <!-- Command bar area (T053) -->
    <div class="cc-layout__commandbar">
      <slot name="commandbar" />
    </div>

    <!-- Status bar area -->
    <footer class="cc-layout__statusbar">
      <slot name="statusbar" />
    </footer>
  </div>
</template>

<style scoped>
.cc-layout {
  display: grid;
  grid-template-rows: var(--cc-header-h, 48px) 1fr var(--cc-commandbar-h, 38px) var(--cc-statusbar-h, 32px);
  grid-template-columns: 1fr;
  height: 100vh;
  overflow: hidden;
  background: var(--cc-bg, #0a0a0a);
  color: var(--cc-text, #e0e0e0);
  font-family: var(--cc-font, ui-monospace, monospace);
}

.cc-layout__header {
  grid-row: 1;
  grid-column: 1;
  border-bottom: 1px solid var(--cc-border, #1a1a1a);
  background: var(--cc-surface, #111);
  z-index: 20;
  flex-shrink: 0;
  min-width: 0;
}

/* The body row holds sidebar + handles + main + right side by side */
.cc-layout__body {
  grid-row: 2;
  grid-column: 1;
  display: flex;
  overflow: hidden;
  min-height: 0;
}

/* Command bar row (T053) */
.cc-layout__commandbar {
  grid-row: 3;
  grid-column: 1;
  z-index: 15;
  min-width: 0;
  overflow: hidden;
}

.cc-layout__sidebar {
  /* width set inline via :style binding */
  flex-shrink: 0;
  border-right: 1px solid var(--cc-border, #1a1a1a);
  background: var(--cc-surface, #111);
  overflow-y: auto;
  overflow-x: hidden;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.cc-layout__main {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  position: relative;
  display: flex;
  flex-direction: column;
}

.cc-layout__right {
  /* width set inline via :style binding */
  flex-shrink: 0;
  border-left: 1px solid var(--cc-border, #1a1a1a);
  background: var(--cc-surface, #111);
  overflow-y: auto;
  overflow-x: hidden;
  min-width: 0;
}

/* ── Resize handles ───────────────────────────────────────────────────────── */
.cc-resize-handle {
  flex-shrink: 0;
  width: 4px;
  height: 100%;
  cursor: col-resize;
  background: var(--cc-border, #1a1a1a);
  position: relative;
  z-index: 10;
  transition: background 0.15s ease;
}

.cc-resize-handle:hover,
.cc-resize-handle:active {
  background: var(--accent-cyan, #00ffcc);
}

/* Wider invisible hit area via a pseudo-element so the handle is easy to grab */
.cc-resize-handle::before {
  content: '';
  position: absolute;
  top: 0;
  left: -4px;
  right: -4px;
  bottom: 0;
  cursor: col-resize;
}

/* ── Narrow-screen: collapse sidebars ─────────────────────────── */
@media (max-width: 768px) {
  .cc-layout__sidebar {
    display: none;
  }
  .cc-layout__right {
    display: none;
  }
  .cc-resize-handle {
    display: none;
  }
}
</style>
