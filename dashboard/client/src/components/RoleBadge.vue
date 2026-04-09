<template>
  <span class="role-badge" :class="`role-${role}`" :title="label">{{ label }}</span>
</template>

<script setup lang="ts">
/**
 * RoleBadge — small colored badge that shows the inferred role of an agent run.
 *
 * @task T033
 * @epic T028
 * @why T030 audit found no role display on run cards. Tac-master's RunSummary
 *      has no explicit role field, so role is inferred from available fields.
 * @what Renders a color-coded pill: orchestrator=purple, lead=blue, worker=green.
 *      Heuristic for role inference (documented here and in inferRole util):
 *        - source_app == "tac-master" AND adw_id present AND phase in known
 *          worker phases (plan_iso, build_iso, test_iso, review_iso, etc.) → worker
 *        - source_app == "tac-master" AND adw_id present AND no worker-phase → lead
 *        - no adw_id, or source_app != "tac-master" → orchestrator
 */

export type AgentRole = "orchestrator" | "lead" | "worker";

const props = defineProps<{
  role: AgentRole;
}>();

const labelMap: Record<AgentRole, string> = {
  orchestrator: "ORCH",
  lead:         "LEAD",
  worker:       "WRKR",
};

import { computed } from "vue";
const label = computed(() => labelMap[props.role] ?? props.role.toUpperCase());
</script>

<style scoped>
.role-badge {
  display: inline-block;
  font-size: 0.65rem;
  font-weight: 700;
  letter-spacing: 0.06em;
  padding: 1px 5px;
  border-radius: 3px;
  text-transform: uppercase;
  font-family: "JetBrains Mono", ui-monospace, monospace;
}

.role-orchestrator {
  background: rgba(168, 85, 247, 0.18);
  color: #a855f7;
  border: 1px solid rgba(168, 85, 247, 0.35);
}

.role-lead {
  background: rgba(59, 130, 246, 0.18);
  color: #60a5fa;
  border: 1px solid rgba(59, 130, 246, 0.35);
}

.role-worker {
  background: rgba(16, 185, 129, 0.18);
  color: #34d399;
  border: 1px solid rgba(16, 185, 129, 0.35);
}
</style>
