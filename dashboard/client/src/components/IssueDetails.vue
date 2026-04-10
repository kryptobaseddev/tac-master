<template>
  <div class="issue-details">
    <div class="panel-header">
      <span class="panel-title">ISSUE DETAILS</span>
      <div class="panel-header-right">
        <span v-if="run" class="run-status-tag" :class="`status-${run.status}`">
          {{ run.status.toUpperCase() }}
        </span>
        <button class="collapse-btn" @click="collapsed = !collapsed" :title="collapsed ? 'Expand' : 'Collapse'">
          {{ collapsed ? '▸' : '▾' }}
        </button>
      </div>
    </div>

    <div v-if="!collapsed">
      <div v-if="!run" class="empty-state">
        <span class="empty-icon">&#9671;</span>
        <span class="empty-text">Select a run to view issue details</span>
      </div>

      <div v-else class="details-body">
        <!-- SUMMARY — max 2 lines with ellipsis -->
        <div class="summary-row">
          <span class="detail-label">SUMMARY</span>
          <span class="detail-value summary-value" :title="summaryText">{{ summaryText }}</span>
        </div>

        <!-- Inline meta row: REF ID · REPO · WORKFLOW -->
        <div class="meta-row">
          <span class="meta-chip ref-chip">#{{ run.issue_number }}</span>
          <span class="meta-sep">·</span>
          <span class="meta-chip" :title="run.adw_id">{{ repoSlug || run.adw_id }}</span>
          <span v-if="run.workflow" class="meta-sep">·</span>
          <span v-if="run.workflow" class="meta-chip wf-chip">{{ run.workflow }}</span>
        </div>

        <!-- VIEW ON GITHUB button -->
        <a
          v-if="githubUrl"
          :href="githubUrl"
          target="_blank"
          rel="noopener noreferrer"
          class="gh-btn"
        >
          <svg class="gh-icon" viewBox="0 0 24 24" fill="currentColor">
            <path
              fill-rule="evenodd"
              d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483
                 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608
                 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338
                 -2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65
                 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337
                 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688
                 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747
                 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z"
              clip-rule="evenodd"
            />
          </svg>
          VIEW ON GITHUB
          <svg class="ext-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6" />
            <polyline points="15 3 21 3 21 9" />
            <line x1="10" y1="14" x2="21" y2="3" />
          </svg>
        </a>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * IssueDetails — compact info card for the selected run's GitHub issue.
 *
 * Compact layout: max ~120px tall. Summary is clamped to 2 lines.
 * Status, repo, and workflow are shown on a single inline meta row.
 * Collapsible via chevron button.
 *
 * @task T038 (updated T054-fixup)
 * @epic T036
 */
import { ref, computed } from "vue";
import { useOrchestratorStore } from "../stores/orchestratorStore";
import type { RunSummary } from "../types";

const props = withDefaults(
  defineProps<{
    run?: RunSummary | null;
  }>(),
  { run: null },
);

const store = useOrchestratorStore();
const collapsed = ref(false);

// Resolve run: prop takes precedence over selected agent in store
const run = computed<RunSummary | null>(() => {
  if (props.run) return props.run;
  const agent = store.selectedAgent;
  if (!agent) return null;
  const meta = agent.metadata ?? {};
  return {
    adw_id: agent.id,
    repo_url: (meta.repo_url as string) ?? "",
    issue_number: (meta.issue_number as number) ?? 0,
    workflow: agent.adw_step ?? "",
    model_set: "",
    status: (meta.run_status as string) ?? agent.status,
    tokens_used: agent.input_tokens + agent.output_tokens,
  } as RunSummary;
});

const repoSlug = computed<string>(() => {
  if (!run.value) return "";
  const url = run.value.repo_url ?? "";
  return url.replace("https://github.com/", "").replace(/\.git$/, "");
});

const githubUrl = computed<string | null>(() => {
  if (!run.value || !run.value.issue_number) return null;
  const slug = repoSlug.value;
  if (!slug) return null;
  return `https://github.com/${slug}/issues/${run.value.issue_number}`;
});

/**
 * Best-effort summary text.
 * Priority: store agent name > workflow + issue number > fallback.
 */
const summaryText = computed<string>(() => {
  if (!run.value) return "—";
  const agent = store.agents.find((a) => a.id === run.value!.adw_id);
  if (agent?.name) return agent.name;
  const slug = repoSlug.value;
  const issue = run.value.issue_number;
  const wf = run.value.workflow;
  return `${wf ? wf + " · " : ""}${slug ? slug + " · " : ""}Issue #${issue}`;
});
</script>

<style scoped>
.issue-details {
  display: flex;
  flex-direction: column;
  background: #0d0f1a;
  border: 1px solid rgba(255, 255, 255, 0.07);
  border-radius: 6px;
  overflow: hidden;
  font-family: var(--font-mono, ui-monospace, monospace);
  flex-shrink: 0;
}

/* ── Header ── */
.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 10px;
  background: #12171e;
  border-bottom: 1px solid rgba(255, 255, 255, 0.07);
  flex-shrink: 0;
}

.panel-title {
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.12em;
  color: #6b7280;
  text-transform: uppercase;
}

.panel-header-right {
  display: flex;
  align-items: center;
  gap: 6px;
}

.run-status-tag {
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.06em;
  padding: 1px 6px;
  border-radius: 3px;
  text-transform: uppercase;
}

.collapse-btn {
  background: none;
  border: none;
  color: #484f58;
  cursor: pointer;
  font-size: 10px;
  line-height: 1;
  padding: 0 2px;
  transition: color 0.15s;
}
.collapse-btn:hover {
  color: #8b949e;
}

.status-running  { background: rgba(16, 185, 129, 0.15); color: #10b981; border: 1px solid rgba(16, 185, 129, 0.3); }
.status-pending  { background: rgba(245, 158, 11, 0.15); color: #f59e0b; border: 1px solid rgba(245, 158, 11, 0.3); }
.status-succeeded{ background: rgba(59, 130, 246, 0.15); color: #3b82f6; border: 1px solid rgba(59, 130, 246, 0.3); }
.status-failed   { background: rgba(239, 68, 68, 0.15);  color: #ef4444; border: 1px solid rgba(239, 68, 68, 0.3); }
.status-aborted  { background: rgba(107, 114, 128, 0.2); color: #6b7280; border: 1px solid rgba(107, 114, 128, 0.3); }

/* ── Empty ── */
.empty-state {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
}

.empty-icon {
  font-size: 14px;
  color: #374151;
}

.empty-text {
  font-size: 10px;
  color: #6b7280;
  margin: 0;
}

/* ── Details body — compact ── */
.details-body {
  display: flex;
  flex-direction: column;
  padding: 6px 0 4px;
}

/* Summary row */
.summary-row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 4px 12px;
}

.detail-label {
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.1em;
  color: #6b7280;
  text-transform: uppercase;
  min-width: 60px;
  flex-shrink: 0;
  padding-top: 1px;
}

.detail-value {
  font-size: 11px;
  color: #e5e7eb;
  line-height: 1.35;
  flex: 1;
  min-width: 0;
}

.summary-value {
  color: #f5f7fa;
  font-weight: 500;
  /* Clamp to 2 lines */
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Inline meta row */
.meta-row {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 3px 12px;
  flex-wrap: wrap;
}

.meta-chip {
  font-size: 10px;
  color: #9ca3af;
  font-family: inherit;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 180px;
}

.ref-chip {
  color: #3b82f6;
  font-weight: 700;
  font-size: 11px;
}

.wf-chip {
  color: #6b7280;
}

.meta-sep {
  color: #374151;
  font-size: 10px;
  flex-shrink: 0;
}

/* ── GitHub button ── */
.gh-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  margin: 4px 12px 6px;
  padding: 5px 10px;
  background: #161b22;
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 4px;
  color: #e5e7eb;
  text-decoration: none;
  font-size: 10px;
  font-family: inherit;
  font-weight: 700;
  letter-spacing: 0.06em;
  transition: all 0.15s;
  cursor: pointer;
}

.gh-btn:hover {
  background: #21262d;
  border-color: rgba(255, 255, 255, 0.25);
  color: #fff;
}

.gh-icon {
  width: 13px;
  height: 13px;
  flex-shrink: 0;
}

.ext-icon {
  width: 10px;
  height: 10px;
  flex-shrink: 0;
  color: #6b7280;
}
</style>
