<script setup lang="ts">
/**
 * RepoSidebar — Left sidebar displaying the repo list and nav links.
 *
 * Data sourced from the orchestrator store (populated from /api/repos via WS).
 * Each repo row shows:
 *   • Status dot: green = has active runs, red = has failed runs, gray = idle
 *   • Repo slug
 *   • Failed count badge (if > 0)
 *
 * Bottom links: ACTIVE_PIPELINES, SYSTEM_LOGS, DOCS
 *
 * Props:
 *   selectedRepoUrl — URL of the currently selected repo
 *   activeTab       — current app tab (used to highlight bottom nav links)
 *
 * Emits:
 *   select-repo  — { url: string } when a repo row is clicked
 *   navigate     — tab string when a bottom link is clicked
 *
 * @task T037
 * @epic T036
 */

import { computed } from "vue";
import { useOrchestratorStore } from "../stores/orchestratorStore";
import type { RepoStatus } from "../types";

const props = defineProps<{
  selectedRepoUrl: string;
  activeTab: string;
}>();

const emit = defineEmits<{
  (e: "select-repo", url: string): void;
  (e: "navigate", tab: string): void;
}>();

const store = useOrchestratorStore();
const repos = computed(() => store.repos);

// ── Status helpers ────────────────────────────────────────────────

type DotClass = "rs-dot--green" | "rs-dot--red" | "rs-dot--gray";

function repoDotClass(repo: RepoStatus): DotClass {
  if (repo.active_runs > 0) return "rs-dot--green";
  if (repo.failed_today > 0) return "rs-dot--red";
  return "rs-dot--gray";
}

function repoStatus(repo: RepoStatus): string {
  if (repo.active_runs > 0) return `${repo.active_runs} active`;
  if (repo.failed_today > 0) return `${repo.failed_today} failed today`;
  return "idle";
}

function repoLabel(repo: RepoStatus): string {
  return repo.slug ?? repo.url.replace("https://github.com/", "").replace(/\.git$/, "");
}

// ── Bottom nav links ──────────────────────────────────────────────
const navLinks = [
  { id: "pipelines", label: "ACTIVE_PIPELINES", tab: "dashboard" },
  { id: "logs",      label: "SYSTEM_LOGS",      tab: "dashboard" },
  { id: "config",    label: "DOCS",             tab: "config"    },
] as const;
</script>

<template>
  <div class="rs">
    <!-- Section header -->
    <div class="rs__section-header">
      <span class="rs__section-label">REPOSITORIES</span>
      <span class="rs__count">{{ repos.length }}</span>
    </div>

    <!-- Repo list -->
    <ul class="rs__repo-list" role="listbox">
      <li
        v-for="repo in repos"
        :key="repo.url"
        class="rs__repo-item"
        :class="{ 'rs__repo-item--selected': selectedRepoUrl === repo.url }"
        role="option"
        :aria-selected="selectedRepoUrl === repo.url"
        :title="`${repoLabel(repo)} — ${repoStatus(repo)}`"
        @click="emit('select-repo', repo.url)"
      >
        <!-- Status dot -->
        <span class="rs-dot" :class="repoDotClass(repo)" />

        <!-- Slug -->
        <span class="rs__repo-slug">{{ repoLabel(repo) }}</span>

        <!-- Failed badge -->
        <span
          v-if="repo.failed_today > 0"
          class="rs__badge rs__badge--red"
          :title="`${repo.failed_today} failed today`"
        >
          {{ repo.failed_today }}
        </span>

        <!-- Active badge -->
        <span
          v-else-if="repo.active_runs > 0"
          class="rs__badge rs__badge--green"
          :title="`${repo.active_runs} active`"
        >
          {{ repo.active_runs }}
        </span>
      </li>

      <!-- Empty state -->
      <li v-if="repos.length === 0" class="rs__empty">
        <span class="rs__empty-text">NO_REPOS</span>
      </li>
    </ul>

    <!-- Divider -->
    <div class="rs__divider" />

    <!-- Bottom nav links -->
    <nav class="rs__nav">
      <button
        v-for="link in navLinks"
        :key="link.id"
        class="rs__nav-link"
        :class="{ 'rs__nav-link--active': activeTab === link.tab && link.id !== 'logs' }"
        @click="emit('navigate', link.tab)"
      >
        <span class="rs__nav-link-arrow">›</span>
        {{ link.label }}
      </button>
    </nav>
  </div>
</template>

<style scoped>
.rs {
  display: flex;
  flex-direction: column;
  height: 100%;
  font-family: var(--cc-font, ui-monospace, monospace);
  background: var(--cc-surface, #111);
  color: var(--cc-text, #e0e0e0);
  overflow: hidden;
}

/* ── Section header ──────────────────────────────────────────── */
.rs__section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px 6px;
  flex-shrink: 0;
}

.rs__section-label {
  font-size: 8px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--cc-text-muted, #666);
}

.rs__count {
  font-size: 9px;
  color: var(--cc-cyan, #00ffcc);
  font-weight: 700;
}

/* ── Repo list ───────────────────────────────────────────────── */
.rs__repo-list {
  list-style: none;
  margin: 0;
  padding: 0;
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  /* Custom scrollbar */
  scrollbar-width: thin;
  scrollbar-color: var(--cc-border-mid, #222) var(--cc-bg, #0a0a0a);
}

.rs__repo-list::-webkit-scrollbar { width: 3px; }
.rs__repo-list::-webkit-scrollbar-track { background: var(--cc-bg, #0a0a0a); }
.rs__repo-list::-webkit-scrollbar-thumb { background: var(--cc-border-mid, #222); border-radius: 2px; }

.rs__repo-item {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 7px 12px;
  cursor: pointer;
  border-left: 2px solid transparent;
  transition: background var(--cc-transition, 0.15s ease),
              border-color var(--cc-transition, 0.15s ease);
  min-height: 32px;
  overflow: hidden;
}

.rs__repo-item:hover {
  background: var(--cc-surface-raised, #161616);
  border-left-color: var(--cc-border-hi, #2a2a2a);
}

.rs__repo-item--selected {
  background: var(--cc-cyan-dim, rgba(0, 255, 204, 0.08));
  border-left-color: var(--cc-cyan, #00ffcc);
}

.rs__repo-item--selected .rs__repo-slug {
  color: var(--cc-cyan, #00ffcc);
}

/* Status dot */
.rs-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}

.rs-dot--green {
  background: var(--cc-green, #00ff66);
  box-shadow: 0 0 6px rgba(0, 255, 102, 0.5);
}

.rs-dot--red {
  background: var(--cc-red, #ff4466);
  box-shadow: 0 0 6px rgba(255, 68, 102, 0.5);
}

.rs-dot--gray {
  background: var(--cc-text-dim, #444);
}

/* Slug text */
.rs__repo-slug {
  font-size: 10px;
  letter-spacing: 0.03em;
  color: var(--cc-text, #e0e0e0);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
  min-width: 0;
}

/* Count badges */
.rs__badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 16px;
  height: 14px;
  padding: 0 4px;
  border-radius: 2px;
  font-size: 8px;
  font-weight: 700;
  flex-shrink: 0;
}

.rs__badge--green {
  background: rgba(0, 255, 102, 0.15);
  color: var(--cc-green, #00ff66);
  border: 1px solid rgba(0, 255, 102, 0.3);
}

.rs__badge--red {
  background: rgba(255, 68, 102, 0.15);
  color: var(--cc-red, #ff4466);
  border: 1px solid rgba(255, 68, 102, 0.3);
}

/* Empty state */
.rs__empty {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px 12px;
}

.rs__empty-text {
  font-size: 9px;
  letter-spacing: 0.1em;
  color: var(--cc-text-dim, #444);
}

/* ── Divider ─────────────────────────────────────────────────── */
.rs__divider {
  height: 1px;
  background: var(--cc-border, #1a1a1a);
  flex-shrink: 0;
  margin: 4px 0;
}

/* ── Bottom nav ──────────────────────────────────────────────── */
.rs__nav {
  display: flex;
  flex-direction: column;
  padding: 4px 0 8px;
  flex-shrink: 0;
}

.rs__nav-link {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 12px;
  background: transparent;
  border: none;
  font-family: var(--cc-font, ui-monospace, monospace);
  font-size: 9px;
  letter-spacing: 0.08em;
  color: var(--cc-text-muted, #666);
  cursor: pointer;
  text-align: left;
  transition: color var(--cc-transition, 0.15s ease),
              background var(--cc-transition, 0.15s ease);
}

.rs__nav-link:hover {
  color: var(--cc-text, #e0e0e0);
  background: var(--cc-surface-raised, #161616);
}

.rs__nav-link--active {
  color: var(--cc-cyan, #00ffcc);
}

.rs__nav-link-arrow {
  font-size: 11px;
  opacity: 0.6;
}
</style>
