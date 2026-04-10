<template>
  <div class="issue-footer" v-if="run">
    <span class="issue-ref">#{{ run.issue_number }}</span>
    <span class="issue-sep">·</span>
    <span class="issue-repo" :title="repoSlug || run.adw_id">{{ repoSlug || run.adw_id }}</span>
    <span v-if="run.workflow" class="issue-sep">·</span>
    <span v-if="run.workflow" class="issue-wf">{{ run.workflow }}</span>
    <span class="issue-sep">·</span>
    <span class="issue-summary" :title="summaryText">{{ summaryText }}</span>
    <span class="issue-spacer" />
    <span class="run-status-tag" :class="`status-${run.status}`">{{ run.status.toUpperCase() }}</span>
    <a
      v-if="githubUrl"
      :href="githubUrl"
      target="_blank"
      rel="noopener noreferrer"
      class="gh-link"
      title="View on GitHub"
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
      View on GitHub
      <svg class="ext-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <polyline points="15 3 21 3 21 9" />
        <line x1="10" y1="14" x2="21" y2="3" />
      </svg>
    </a>
  </div>
  <div v-else class="issue-footer issue-footer-empty">
    <span class="issue-empty-hint">No active run selected</span>
  </div>
</template>

<script setup lang="ts">
/**
 * IssueDetails — slim one-line footer bar showing issue metadata + GitHub link.
 *
 * All information is displayed on a single horizontal row:
 *   #3 · kryptobaseddev/tac-master · workflow · summary    [STATUS] [View on GitHub →]
 *
 * @task T054
 * @epic T036
 */
import { computed } from "vue";
import { useOrchestratorStore } from "../stores/orchestratorStore";
import type { RunSummary } from "../types";

const props = withDefaults(
  defineProps<{
    run?: RunSummary | null;
  }>(),
  { run: null },
);

const store = useOrchestratorStore();

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

const summaryText = computed<string>(() => {
  if (!run.value) return "—";
  const agent = store.agents.find((a) => a.id === run.value!.adw_id);
  if (agent?.name) return agent.name;
  const slug = repoSlug.value;
  const issue = run.value.issue_number;
  return `${slug ? slug + " · " : ""}Issue #${issue}`;
});
</script>

<style scoped>
.issue-footer {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 0 12px;
  height: 36px;
  flex-shrink: 0;
  background: #0d0f1a;
  border: 1px solid rgba(255, 255, 255, 0.07);
  border-radius: 6px;
  font-family: var(--font-mono, ui-monospace, monospace);
  overflow: hidden;
  min-width: 0;
}

.issue-footer-empty {
  justify-content: center;
}

.issue-empty-hint {
  font-size: 10px;
  color: #484f58;
}

.issue-ref {
  font-size: 11px;
  font-weight: 700;
  color: #3b82f6;
  flex-shrink: 0;
}

.issue-sep {
  font-size: 10px;
  color: #374151;
  flex-shrink: 0;
}

.issue-repo {
  font-size: 10px;
  color: #9ca3af;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 180px;
  flex-shrink: 0;
}

.issue-wf {
  font-size: 10px;
  color: #6b7280;
  white-space: nowrap;
  flex-shrink: 0;
}

.issue-summary {
  font-size: 10px;
  color: #e5e7eb;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}

.issue-spacer {
  flex: 1;
  min-width: 8px;
}

.run-status-tag {
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.06em;
  padding: 2px 6px;
  border-radius: 3px;
  text-transform: uppercase;
  flex-shrink: 0;
  white-space: nowrap;
}

.status-running   { background: rgba(16, 185, 129, 0.15);  color: #10b981; border: 1px solid rgba(16, 185, 129, 0.3); }
.status-pending   { background: rgba(245, 158, 11, 0.15);  color: #f59e0b; border: 1px solid rgba(245, 158, 11, 0.3); }
.status-succeeded { background: rgba(59, 130, 246, 0.15);  color: #3b82f6; border: 1px solid rgba(59, 130, 246, 0.3); }
.status-failed    { background: rgba(239, 68, 68, 0.15);   color: #ef4444; border: 1px solid rgba(239, 68, 68, 0.3); }
.status-aborted   { background: rgba(107, 114, 128, 0.2);  color: #6b7280; border: 1px solid rgba(107, 114, 128, 0.3); }

.gh-link {
  display: flex;
  align-items: center;
  gap: 4px;
  color: #8b949e;
  text-decoration: none;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.04em;
  flex-shrink: 0;
  padding: 3px 7px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 4px;
  transition: all 0.15s;
  white-space: nowrap;
}

.gh-link:hover {
  color: #e5e7eb;
  border-color: rgba(255, 255, 255, 0.22);
  background: rgba(255, 255, 255, 0.04);
}

.gh-icon {
  width: 12px;
  height: 12px;
  flex-shrink: 0;
}

.ext-icon {
  width: 9px;
  height: 9px;
  flex-shrink: 0;
}
</style>
