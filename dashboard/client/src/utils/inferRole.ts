/**
 * inferRole — heuristic to derive an agent role from available run fields.
 *
 * @task T033
 * @epic T028
 * @why Tac-master's RunSummary schema has no explicit role field. Role must be
 *      inferred from source_app and workflow/phase to avoid a schema migration.
 * @what Returns "orchestrator" | "lead" | "worker" based on:
 *        - No adw_id → orchestrator (the tac-master daemon itself)
 *        - adw_id present AND workflow matches a worker-phase pattern → worker
 *        - adw_id present AND workflow is an orchestrator-level workflow → lead
 *
 * Worker phases (tac-master naming convention as of 2026-04):
 *   plan_iso, build_iso, test_iso, review_iso, commit_iso, deploy_iso
 * Lead/orchestrator-level workflows contain no "_iso" suffix.
 */

export type AgentRole = "orchestrator" | "lead" | "worker";

const WORKER_PHASE_PATTERN = /_iso$/i;

/**
 * Infer the role of a run given its workflow name and adw_id.
 *
 * @param adwId   - The adw_id of the run (may be null/undefined for the daemon)
 * @param workflow - The workflow field from RunSummary (e.g. "plan_iso", "tac-master")
 */
export function inferRole(adwId: string | null | undefined, workflow: string | null | undefined): AgentRole {
  if (!adwId) return "orchestrator";
  if (workflow && WORKER_PHASE_PATTERN.test(workflow)) return "worker";
  return "lead";
}
