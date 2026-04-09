<script setup lang="ts">
/**
 * HeaderBar — Command Center top navigation bar.
 *
 * Left:   COMMAND_CENTER branding + WebSocket connection dot
 * Center: Dashboard / Repos / Config nav tabs
 * Right:  Current repo selector dropdown + notification + settings icons
 *
 * Props:
 *   activeTab      — currently active tab string
 *   currentRepoUrl — URL of the selected repo (for the selector)
 *
 * Emits:
 *   update:activeTab      — when a nav tab is clicked
 *   update:currentRepoUrl — when the repo selector changes
 *
 * @task T037
 * @epic T036
 */

import { computed } from "vue";
import { useOrchestratorStore } from "../stores/orchestratorStore";

// ── Props & emits ────────────────────────────────────────────────
const props = defineProps<{
  activeTab: string;
  currentRepoUrl: string;
}>();

const emit = defineEmits<{
  (e: "update:activeTab", tab: string): void;
  (e: "update:currentRepoUrl", url: string): void;
}>();

// ── Store ────────────────────────────────────────────────────────
const store = useOrchestratorStore();

const isConnected = computed(() => store.isConnected);
const repos = computed(() => store.repos);

// ── Nav tabs ─────────────────────────────────────────────────────
const tabs = [
  { id: "dashboard", label: "Dashboard" },
  { id: "repos",     label: "Repos"     },
  { id: "config",    label: "Config"    },
] as const;

function setTab(id: string) {
  emit("update:activeTab", id);
}

// ── Repo selector ────────────────────────────────────────────────
const currentRepoLabel = computed(() => {
  const repo = repos.value.find((r) => r.url === props.currentRepoUrl);
  if (repo) return repo.slug ?? shortSlug(repo.url);
  if (repos.value.length > 0) return shortSlug(repos.value[0].url);
  return "NO_REPO";
});

function shortSlug(url: string): string {
  return url.replace("https://github.com/", "").replace(/\.git$/, "");
}

function onRepoChange(e: Event) {
  const select = e.target as HTMLSelectElement;
  emit("update:currentRepoUrl", select.value);
}
</script>

<template>
  <div class="hb">
    <!-- ── Left: branding ─────────────────────────────── -->
    <div class="hb__left">
      <span class="hb__brand">
        <span class="hb__brand-accent">TAC</span>-MASTER
      </span>
      <span
        class="cc-dot"
        :class="isConnected ? 'cc-dot-green cc-pulse' : 'cc-dot-red'"
        :title="isConnected ? 'WebSocket connected' : 'Reconnecting…'"
      />
    </div>

    <!-- ── Center: nav tabs ───────────────────────────── -->
    <nav class="hb__nav" role="tablist">
      <button
        v-for="tab in tabs"
        :key="tab.id"
        role="tab"
        :aria-selected="activeTab === tab.id"
        class="hb__tab"
        :class="{ 'hb__tab--active': activeTab === tab.id }"
        @click="setTab(tab.id)"
      >
        {{ tab.label.toUpperCase() }}
      </button>
    </nav>

    <!-- ── Right: repo selector + icons ──────────────── -->
    <div class="hb__right">
      <div class="hb__repo-selector">
        <span class="hb__repo-label">CURRENT_REPO</span>
        <div class="hb__repo-dropdown-wrap">
          <select
            class="hb__repo-dropdown"
            :value="currentRepoUrl || (repos[0]?.url ?? '')"
            @change="onRepoChange"
            :title="currentRepoLabel"
          >
            <option
              v-for="repo in repos"
              :key="repo.url"
              :value="repo.url"
            >
              {{ repo.slug ?? shortSlug(repo.url) }}
            </option>
            <option v-if="repos.length === 0" value="">— no repos —</option>
          </select>
          <span class="hb__repo-caret">▾</span>
        </div>
      </div>

      <!-- Notification icon -->
      <button class="hb__icon-btn" title="Notifications" aria-label="Notifications">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/>
          <path d="M13.73 21a2 2 0 0 1-3.46 0"/>
        </svg>
      </button>

      <!-- Settings icon -->
      <button
        class="hb__icon-btn"
        title="Settings"
        aria-label="Settings"
        @click="setTab('config')"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="12" cy="12" r="3"/>
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
        </svg>
      </button>
    </div>
  </div>
</template>

<style scoped>
.hb {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 100%;
  padding: 0 16px;
  gap: 12px;
  font-family: var(--cc-font, ui-monospace, monospace);
}

/* ── Branding ────────────────────────────────────────────────── */
.hb__left {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

.hb__brand {
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: var(--cc-text, #e0e0e0);
  white-space: nowrap;
}

.hb__brand-accent {
  color: var(--cc-cyan, #00ffcc);
  text-shadow: 0 0 10px rgba(0, 255, 204, 0.4);
}

/* ── Nav tabs ────────────────────────────────────────────────── */
.hb__nav {
  display: flex;
  gap: 2px;
  flex-shrink: 0;
}

.hb__tab {
  background: transparent;
  border: 1px solid transparent;
  color: var(--cc-text-muted, #666);
  font-size: 10px;
  font-family: var(--cc-font, ui-monospace, monospace);
  letter-spacing: 0.1em;
  padding: 5px 14px;
  border-radius: 3px;
  cursor: pointer;
  transition: all var(--cc-transition, 0.15s ease);
}

.hb__tab:hover {
  color: var(--cc-text, #e0e0e0);
  background: var(--cc-surface-raised, #161616);
  border-color: var(--cc-border-mid, #222);
}

.hb__tab--active {
  color: var(--cc-cyan, #00ffcc);
  background: var(--cc-cyan-dim, rgba(0, 255, 204, 0.1));
  border-color: rgba(0, 255, 204, 0.3);
  text-shadow: 0 0 8px rgba(0, 255, 204, 0.3);
}

/* ── Right cluster ───────────────────────────────────────────── */
.hb__right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

/* Repo selector */
.hb__repo-selector {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 1px;
}

.hb__repo-label {
  font-size: 8px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--cc-text-muted, #666);
}

.hb__repo-dropdown-wrap {
  position: relative;
  display: flex;
  align-items: center;
}

.hb__repo-dropdown {
  appearance: none;
  -webkit-appearance: none;
  background: var(--cc-surface-raised, #161616);
  border: 1px solid var(--cc-border-mid, #222);
  color: var(--cc-cyan, #00ffcc);
  font-family: var(--cc-font, ui-monospace, monospace);
  font-size: 10px;
  letter-spacing: 0.06em;
  padding: 3px 24px 3px 8px;
  border-radius: 3px;
  cursor: pointer;
  max-width: 200px;
  transition: border-color var(--cc-transition, 0.15s ease);
}

.hb__repo-dropdown:hover,
.hb__repo-dropdown:focus {
  border-color: var(--cc-cyan, #00ffcc);
  outline: none;
}

.hb__repo-dropdown option {
  background: var(--cc-surface, #111);
  color: var(--cc-text, #e0e0e0);
}

.hb__repo-caret {
  position: absolute;
  right: 7px;
  font-size: 9px;
  color: var(--cc-text-muted, #666);
  pointer-events: none;
}

/* Icon buttons */
.hb__icon-btn {
  background: transparent;
  border: 1px solid transparent;
  color: var(--cc-text-muted, #666);
  padding: 5px;
  border-radius: 4px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all var(--cc-transition, 0.15s ease);
}

.hb__icon-btn:hover {
  color: var(--cc-cyan, #00ffcc);
  border-color: var(--cc-border-mid, #222);
  background: var(--cc-surface-raised, #161616);
}

/* cc-dot & cc-pulse defined in command-center.css */
.cc-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.cc-dot-green { background: var(--cc-green, #00ff66); box-shadow: 0 0 8px rgba(0, 255, 102, 0.4); }
.cc-dot-red   { background: var(--cc-red,  #ff4466); box-shadow: 0 0 6px rgba(255, 68, 102, 0.5); }

@keyframes cc-pulse {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0.35; }
}
.cc-pulse { animation: cc-pulse 1.8s ease-in-out infinite; }
</style>
