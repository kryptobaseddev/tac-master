<script setup lang="ts">
import { computed } from "vue";
import { useOrchestratorStore } from "../stores/orchestratorStore";
import type { RepoStatus, Agent } from "../types";

const store = useOrchestratorStore();

const emit = defineEmits<{
  (e: "select-run", id: string): void;
}>();

const sortedRepos = computed<RepoStatus[]>(() =>
  [...store.repos].sort((a, b) => {
    if (a.is_self !== b.is_self) return a.is_self ? -1 : 1;
    return a.slug.localeCompare(b.slug);
  }),
);

// Group runs by repo_url for display under each repo card
const runsByRepo = computed<Record<string, Agent[]>>(() => {
  const m: Record<string, Agent[]> = {};
  for (const agent of store.agents) {
    const repoUrl = String(agent.metadata?.repo_url ?? "");
    if (!repoUrl) continue;
    if (!m[repoUrl]) m[repoUrl] = [];
    m[repoUrl].push(agent);
  }
  // Sort each repo's runs by started_at desc
  for (const url in m) {
    m[url].sort((a, b) => {
      const aT = (a.metadata?.started_at_unix as number) ?? 0;
      const bT = (b.metadata?.started_at_unix as number) ?? 0;
      return bT - aT;
    });
  }
  return m;
});

function repoRuns(repo: RepoStatus): Agent[] {
  return runsByRepo.value[repo.url] ?? [];
}

function fmtTime(ts: number | null | undefined): string {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  return d.toLocaleString();
}

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function statusColor(s: string | null): string {
  return (
    {
      executing: "#3b82f6",
      waiting: "#f59e0b",
      idle: "#9ca3af",
      complete: "#10b981",
      blocked: "#ef4444",
    }[s ?? "idle"] ?? "#9ca3af"
  );
}

function borderFor(repo: RepoStatus): string {
  if (repo.active_runs > 0) return "#3b82f6";
  if (repo.failed_today > 0 && repo.completed_today === 0) return "#ef4444";
  if (repo.completed_today > 0) return "#10b981";
  return "#374151";
}

function onPickRun(adwId: string) {
  store.selectAgent(adwId);
  emit("select-run", adwId);
}
</script>

<template>
  <div class="repo-board">
    <header class="rb-header">
      <h1 class="rb-title">Repositories</h1>
      <p class="rb-sub">
        {{ sortedRepos.length }} allowlisted repo(s) ·
        {{ store.agents.length }} run(s) known
      </p>
    </header>

    <div class="rb-grid">
      <article
        v-for="r in sortedRepos"
        :key="r.url"
        class="rb-card"
        :style="{ borderLeftColor: borderFor(r) }"
      >
        <header class="rb-card-head">
          <div class="rb-card-title-row">
            <h2 class="rb-card-slug">{{ r.slug }}</h2>
            <span v-if="r.is_self" class="rb-badge rb-badge-self">SELF</span>
            <span v-if="r.auto_merge" class="rb-badge rb-badge-zte">ZTE</span>
            <span
              v-if="r.active_runs > 0"
              class="rb-badge rb-badge-live"
            >{{ r.active_runs }} LIVE</span>
          </div>
          <a :href="r.url" target="_blank" class="rb-card-url">{{ r.url }}</a>
        </header>

        <dl class="rb-stats">
          <div><dt>workflow</dt><dd>{{ r.default_workflow }}</dd></div>
          <div><dt>model set</dt><dd>{{ r.model_set }}</dd></div>
          <div>
            <dt>✓ today</dt>
            <dd class="good">{{ r.completed_today }}</dd>
          </div>
          <div>
            <dt>✗ today</dt>
            <dd class="bad">{{ r.failed_today }}</dd>
          </div>
          <div>
            <dt>tokens</dt>
            <dd>{{ fmtTokens(r.tokens_today) }}</dd>
          </div>
          <div>
            <dt>cost</dt>
            <dd>${{ r.cost_today_usd.toFixed(4) }}</dd>
          </div>
          <div>
            <dt>last poll</dt>
            <dd>{{ fmtTime(r.last_polled_at) }}</dd>
          </div>
          <div>
            <dt>last activity</dt>
            <dd>{{ fmtTime(r.last_activity_at) }}</dd>
          </div>
        </dl>

        <!-- Active / recent runs in this repo -->
        <section v-if="repoRuns(r).length > 0" class="rb-runs">
          <h3 class="rb-runs-title">Recent runs ({{ repoRuns(r).length }})</h3>
          <ul class="rb-runs-list">
            <li
              v-for="agent in repoRuns(r).slice(0, 8)"
              :key="agent.id"
              class="rb-run"
              @click="onPickRun(agent.id)"
            >
              <span
                class="rb-run-dot"
                :style="{ background: statusColor(agent.status) }"
              />
              <span class="rb-run-id">{{ agent.id }}</span>
              <span class="rb-run-issue">#{{ agent.metadata?.issue_number }}</span>
              <span class="rb-run-wf">{{ agent.adw_step }}</span>
              <span class="rb-run-status">{{ agent.status }}</span>
            </li>
          </ul>
        </section>
        <p v-else class="rb-no-runs">No runs yet for this repo.</p>
      </article>

      <p v-if="sortedRepos.length === 0" class="rb-empty">
        No repos loaded. If the orchestrator SQLite isn't readable, the
        /api/repos endpoint returns an empty list.
      </p>
    </div>
  </div>
</template>

<style scoped>
.repo-board {
  padding: 20px 24px;
  color: var(--color-text-primary, #e4e7eb);
  font-family: var(--font-mono, ui-monospace, monospace);
}
.rb-header {
  margin-bottom: 16px;
}
.rb-title {
  font-size: 18px;
  margin: 0 0 4px;
  color: #f5f7fa;
}
.rb-sub {
  color: #6b7280;
  font-size: 11px;
  margin: 0;
}
.rb-grid {
  display: grid;
  gap: 14px;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
}
.rb-card {
  background: var(--color-bg-secondary, #1a1a1a);
  border: 1px solid var(--color-border, #2a2a2a);
  border-left: 4px solid #374151;
  border-radius: 6px;
  padding: 14px 16px;
}
.rb-card-head {
  margin-bottom: 12px;
}
.rb-card-title-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.rb-card-slug {
  font-size: 14px;
  margin: 0;
  color: #f5f7fa;
  font-weight: 700;
}
.rb-card-url {
  display: block;
  margin-top: 3px;
  color: #6b7280;
  font-size: 10px;
  text-decoration: none;
  word-break: break-all;
}
.rb-card-url:hover {
  color: #3b82f6;
}
.rb-badge {
  font-size: 9px;
  padding: 2px 6px;
  border-radius: 3px;
  letter-spacing: 0.05em;
  font-weight: 700;
}
.rb-badge-self {
  background: #a855f722;
  color: #a855f7;
}
.rb-badge-zte {
  background: #2a2a2a;
  color: #e4e7eb;
}
.rb-badge-live {
  background: #3b82f622;
  color: #3b82f6;
}
.rb-stats {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px 14px;
  margin: 0 0 12px;
}
.rb-stats > div {
  display: flex;
  justify-content: space-between;
  font-size: 10px;
}
.rb-stats dt {
  color: #6b7280;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.rb-stats dd {
  margin: 0;
  color: #e4e7eb;
  font-weight: 600;
}
.rb-stats dd.good {
  color: #10b981;
}
.rb-stats dd.bad {
  color: #ef4444;
}
.rb-runs {
  border-top: 1px solid var(--color-border, #2a2a2a);
  padding-top: 10px;
}
.rb-runs-title {
  font-size: 10px;
  color: #6b7280;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin: 0 0 6px;
}
.rb-runs-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.rb-run {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 6px;
  border-radius: 3px;
  cursor: pointer;
  font-size: 10px;
}
.rb-run:hover {
  background: #2a2a2a;
}
.rb-run-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}
.rb-run-id {
  font-family: inherit;
  color: #a855f7;
  width: 70px;
}
.rb-run-issue {
  color: #6b7280;
  width: 36px;
}
.rb-run-wf {
  color: #e4e7eb;
  flex: 1;
}
.rb-run-status {
  color: #6b7280;
  font-style: italic;
}
.rb-no-runs {
  font-size: 10px;
  color: #6b7280;
  font-style: italic;
  margin: 0;
}
.rb-empty {
  grid-column: 1 / -1;
  color: #6b7280;
  font-size: 11px;
  text-align: center;
  padding: 40px;
}
</style>
