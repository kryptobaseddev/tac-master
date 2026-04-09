<script setup lang="ts">
/**
 * CommandCenterLayout — master CSS Grid shell for the Command Center redesign.
 *
 * Provides four named areas:
 *   header     — full-width top bar (HeaderBar)
 *   sidebar    — left navigation column (RepoSidebar)
 *   main       — scrollable content area (router-view or panels)
 *   statusbar  — full-width bottom KPI bar (StatusBar)
 *
 * Usage:
 *   <CommandCenterLayout>
 *     <template #header>   <HeaderBar  /> </template>
 *     <template #sidebar>  <RepoSidebar /> </template>
 *     <template #main>     <router-view /> </template>
 *     <template #statusbar><StatusBar  /> </template>
 *   </CommandCenterLayout>
 *
 * @task T037
 * @epic T036
 */

// No props — pure layout shell.
</script>

<template>
  <div class="cc-layout">
    <!-- Header area -->
    <header class="cc-layout__header">
      <slot name="header" />
    </header>

    <!-- Body row: sidebar + main -->
    <div class="cc-layout__body">
      <aside class="cc-layout__sidebar">
        <slot name="sidebar" />
      </aside>

      <main class="cc-layout__main">
        <slot name="main" />
      </main>
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
  grid-template-rows: var(--cc-header-h, 48px) 1fr var(--cc-statusbar-h, 32px);
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

/* The body row holds sidebar + main side by side */
.cc-layout__body {
  grid-row: 2;
  grid-column: 1;
  display: flex;
  overflow: hidden;
  min-height: 0;
}

.cc-layout__sidebar {
  width: var(--cc-sidebar-w, 220px);
  flex-shrink: 0;
  border-right: 1px solid var(--cc-border, #1a1a1a);
  background: var(--cc-surface, #111);
  overflow-y: auto;
  overflow-x: hidden;
}

.cc-layout__main {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  position: relative;
}

.cc-layout__statusbar {
  grid-row: 3;
  grid-column: 1;
  border-top: 1px solid var(--cc-border, #1a1a1a);
  background: var(--cc-surface, #111);
  z-index: 20;
  flex-shrink: 0;
  min-width: 0;
}

/* ── Narrow-screen: collapse sidebar ─────────────────────────── */
@media (max-width: 768px) {
  .cc-layout__sidebar {
    display: none;
  }
}
</style>
