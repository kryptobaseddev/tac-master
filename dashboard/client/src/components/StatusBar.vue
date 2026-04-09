<script setup lang="ts">
/**
 * StatusBar — bottom KPI bar for the Command Center.
 *
 * Shows real-time aggregate statistics:
 *   • LIVE_RUNS  — count of runs with status=running (from store.runningAgents)
 *   • REPOS      — total repos tracked (from store.repos)
 *   • RUNS       — total runs loaded (from store.agents)
 *   • TOKENS/DAY — sum of tokens_today across all repos
 *   • COST/DAY   — sum of cost_today_usd across all repos
 *
 * Data is live because the store is kept in sync by the WebSocket.
 * A 10-second polling fallback via /api/stats is also provided for
 * environments where the WS broadcast may lag.
 *
 * @task T037
 * @epic T036
 */

import { ref, computed, onMounted, onUnmounted } from "vue";
import { useOrchestratorStore } from "../stores/orchestratorStore";

const store = useOrchestratorStore();

// ── Derived KPIs from the store ────────────────────────────────────
const liveRuns  = computed(() => store.runningAgents.length);
const totalRepos = computed(() => store.repos.length);
const totalRuns  = computed(() => store.agents.length);

const tokensToday = computed(() =>
  store.repos.reduce((sum, r) => sum + (r.tokens_today ?? 0), 0),
);

const costToday = computed(() =>
  store.repos.reduce((sum, r) => sum + (r.cost_today_usd ?? 0), 0),
);

// ── /api/stats polling fallback (10 s) ────────────────────────────
// This supplements the WS data with server-aggregated stats so the
// bar shows correct values even if the WS hasn't broadcast an update.
const statsOverride = ref<{
  live_runs: number;
  total_repos: number;
  tokens_today: number;
  cost_today_usd: number;
  total_runs: number;
} | null>(null);

async function fetchStats() {
  try {
    const res = await fetch("/api/stats");
    if (res.ok) {
      statsOverride.value = await res.json();
    }
  } catch {
    // silent — store values remain authoritative
  }
}

let pollTimer: ReturnType<typeof setInterval> | null = null;

onMounted(() => {
  fetchStats();
  pollTimer = setInterval(fetchStats, 10_000);
});

onUnmounted(() => {
  if (pollTimer !== null) clearInterval(pollTimer);
});

// ── Display values: prefer store (real-time) then statsOverride ────
const displayLiveRuns = computed(() =>
  liveRuns.value > 0 || !statsOverride.value
    ? liveRuns.value
    : statsOverride.value.live_runs,
);

const displayRepos = computed(() =>
  totalRepos.value > 0 || !statsOverride.value
    ? totalRepos.value
    : statsOverride.value.total_repos,
);

const displayRuns = computed(() =>
  totalRuns.value > 0 || !statsOverride.value
    ? totalRuns.value
    : statsOverride.value.total_runs,
);

const displayTokens = computed(() =>
  tokensToday.value > 0 || !statsOverride.value
    ? tokensToday.value
    : statsOverride.value.tokens_today,
);

const displayCost = computed(() =>
  costToday.value > 0 || !statsOverride.value
    ? costToday.value
    : statsOverride.value.cost_today_usd,
);

// ── Formatters ─────────────────────────────────────────────────────
function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000)     return `${(n / 1_000).toFixed(1)}K`;
  return String(Math.round(n));
}

function fmtRuns(n: number): string {
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function fmtCost(n: number): string {
  return `$${n.toFixed(2)}`;
}
</script>

<template>
  <div class="sb">
    <!-- Live runs indicator with pulsing dot -->
    <div class="sb__stat sb__stat--primary">
      <span
        class="sb__live-dot"
        :class="displayLiveRuns > 0 ? 'sb__live-dot--active' : 'sb__live-dot--idle'"
      />
      <span class="sb__label">LIVE_RUNS</span>
      <span class="sb__value sb__value--green">{{ displayLiveRuns }}</span>
    </div>

    <span class="sb__sep" />

    <div class="sb__stat">
      <span class="sb__label">REPOS</span>
      <span class="sb__value">{{ displayRepos }}</span>
    </div>

    <span class="sb__sep" />

    <div class="sb__stat">
      <span class="sb__label">RUNS</span>
      <span class="sb__value">{{ fmtRuns(displayRuns) }}</span>
    </div>

    <span class="sb__sep" />

    <div class="sb__stat">
      <span class="sb__label">TOKENS/DAY</span>
      <span class="sb__value">{{ fmtTokens(displayTokens) }}</span>
    </div>

    <span class="sb__sep" />

    <div class="sb__stat">
      <span class="sb__label">COST/DAY</span>
      <span class="sb__value">{{ fmtCost(displayCost) }}</span>
    </div>

    <!-- Spacer pushes version tag to the right -->
    <div class="sb__spacer" />

    <div class="sb__version">
      TAC-MASTER v2
    </div>
  </div>
</template>

<style scoped>
.sb {
  display: flex;
  align-items: center;
  gap: 0;
  height: 100%;
  padding: 0 14px;
  font-family: var(--cc-font, ui-monospace, monospace);
  background: var(--cc-surface, #111);
  color: var(--cc-text, #e0e0e0);
  overflow: hidden;
}

/* ── Stat cell ───────────────────────────────────────────────── */
.sb__stat {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0 10px;
}

.sb__stat--primary {
  padding-left: 0;
}

.sb__label {
  font-size: 8px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--cc-text-muted, #666);
  white-space: nowrap;
}

.sb__value {
  font-size: 11px;
  font-weight: 700;
  color: var(--cc-text, #e0e0e0);
  white-space: nowrap;
}

.sb__value--green {
  color: var(--cc-green, #00ff66);
  text-shadow: 0 0 8px rgba(0, 255, 102, 0.4);
}

/* ── Live dot ────────────────────────────────────────────────── */
.sb__live-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}

.sb__live-dot--active {
  background: var(--cc-green, #00ff66);
  box-shadow: 0 0 6px rgba(0, 255, 102, 0.5);
  animation: sb-pulse 1.8s ease-in-out infinite;
}

.sb__live-dot--idle {
  background: var(--cc-text-dim, #444);
}

@keyframes sb-pulse {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0.35; }
}

/* ── Separator ───────────────────────────────────────────────── */
.sb__sep {
  width: 1px;
  height: 16px;
  background: var(--cc-border-mid, #222);
  flex-shrink: 0;
}

/* ── Spacer ──────────────────────────────────────────────────── */
.sb__spacer {
  flex: 1;
}

/* ── Version tag ─────────────────────────────────────────────── */
.sb__version {
  font-size: 8px;
  letter-spacing: 0.1em;
  color: var(--cc-text-dim, #444);
  white-space: nowrap;
}
</style>
