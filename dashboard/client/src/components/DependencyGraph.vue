<template>
  <div class="dep-graph-wrap">
    <!-- Mode toggle -->
    <div class="dep-graph-toolbar">
      <button
        class="dep-mode-btn"
        :class="{ 'dep-mode-active': mode === 'task' }"
        :disabled="!hasEpic"
        @click="mode = 'task'"
        title="Show task dependency graph for selected epic"
      >TASK DEPS</button>
      <span class="dep-mode-sep">|</span>
      <button
        class="dep-mode-btn"
        :class="{ 'dep-mode-active': mode === 'pipeline' }"
        @click="mode = 'pipeline'"
        title="Show pipeline phase flow for selected run"
      >PIPELINE FLOW</button>
    </div>

    <!-- Task dependency graph -->
    <div v-if="mode === 'task'" class="dep-graph-svg-area">
      <div v-if="!hasEpic" class="dep-graph-empty">
        <span class="dep-empty-icon">&#9671;</span>
        <span class="dep-empty-hint">Select an epic in the task tree to view dependency graph</span>
      </div>
      <div v-else-if="taskLoading" class="dep-graph-empty">
        <span class="dep-empty-hint">Loading tasks...</span>
      </div>
      <div v-else-if="graphNodes.length === 0" class="dep-graph-empty">
        <span class="dep-empty-icon">&#9671;</span>
        <span class="dep-empty-hint">No tasks found for {{ epicId }}</span>
      </div>
      <div v-else class="dep-svg-container" ref="svgContainerRef">
        <svg
          :viewBox="`0 0 ${svgWidth} ${svgHeight}`"
          :width="svgWidth"
          :height="svgHeight"
          class="dep-svg"
          xmlns="http://www.w3.org/2000/svg"
        >
          <defs>
            <marker id="arrowhead" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
              <polygon points="0 0, 8 3, 0 6" :fill="COLORS.edge" />
            </marker>
            <marker id="arrowhead-done" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
              <polygon points="0 0, 8 3, 0 6" :fill="COLORS.done" />
            </marker>
            <marker id="arrowhead-active" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
              <polygon points="0 0, 8 3, 0 6" :fill="COLORS.active" />
            </marker>
            <marker id="arrowhead-blocked" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
              <polygon points="0 0, 8 3, 0 6" :fill="COLORS.blocked" />
            </marker>
          </defs>

          <!-- Edges -->
          <g class="dep-edges">
            <path
              v-for="(edge, i) in graphEdges"
              :key="`edge-${i}`"
              :d="edgePath(edge)"
              fill="none"
              :stroke="edgeStroke(edge)"
              stroke-width="1.5"
              stroke-opacity="0.6"
              :marker-end="edgeMarker(edge)"
            />
          </g>

          <!-- Nodes -->
          <g class="dep-nodes">
            <g
              v-for="node in graphNodes"
              :key="node.id"
              :transform="`translate(${node.x}, ${node.y})`"
              class="dep-node"
              :class="`dep-node-${node.normStatus}`"
              @click="openTask(node.id)"
              @mouseenter="hoveredNode = node.id"
              @mouseleave="hoveredNode = null"
              style="cursor: pointer;"
            >
              <rect
                :width="NODE_W"
                :height="NODE_H"
                rx="6"
                :fill="statusFill(node.normStatus)"
                :stroke="statusStroke(node.normStatus)"
                stroke-width="1.5"
                :opacity="hoveredNode === node.id ? 1 : 0.85"
              />
              <text
                :x="NODE_W / 2"
                y="15"
                text-anchor="middle"
                :fill="statusStroke(node.normStatus)"
                font-family="ui-monospace, 'Cascadia Code', Menlo, Consolas, monospace"
                font-size="10"
                font-weight="700"
                letter-spacing="0.04em"
              >{{ node.id }}</text>
              <text
                :x="NODE_W / 2"
                y="27"
                text-anchor="middle"
                fill="#9ca3af"
                font-family="ui-monospace, 'Cascadia Code', Menlo, Consolas, monospace"
                font-size="9"
              >{{ truncate(node.title, 17) }}</text>
            </g>
          </g>

          <!-- Tooltip -->
          <g v-if="hoveredNode && tooltipNode" class="dep-tooltip">
            <rect
              :x="tooltipX"
              :y="tooltipY"
              :width="tooltipWidth"
              height="28"
              rx="4"
              fill="#1e2229"
              stroke="#374151"
              stroke-width="1"
            />
            <text
              :x="tooltipX + tooltipWidth / 2"
              :y="tooltipY + 17"
              text-anchor="middle"
              fill="#e6edf3"
              font-family="ui-monospace, 'Cascadia Code', Menlo, Consolas, monospace"
              font-size="10"
            >{{ tooltipNode.title }}</text>
          </g>
        </svg>
      </div>
    </div>

    <!-- Pipeline phase flow graph -->
    <div v-else class="dep-graph-svg-area">
      <div v-if="!hasRun" class="dep-graph-empty">
        <span class="dep-empty-icon">&#9671;</span>
        <span class="dep-empty-hint">Select a run to view pipeline flow</span>
      </div>
      <div v-else class="dep-svg-container">
        <svg
          :viewBox="`0 0 ${pipelineSvgWidth} ${pipelineSvgHeight}`"
          :width="pipelineSvgWidth"
          :height="pipelineSvgHeight"
          class="dep-svg"
          xmlns="http://www.w3.org/2000/svg"
        >
          <defs>
            <marker id="pipe-arrow" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
              <polygon points="0 0, 8 3, 0 6" fill="#374151" />
            </marker>
            <marker id="pipe-arrow-done" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
              <polygon points="0 0, 8 3, 0 6" :fill="COLORS.done" />
            </marker>
            <marker id="pipe-arrow-active" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
              <polygon points="0 0, 8 3, 0 6" :fill="COLORS.active" />
            </marker>
          </defs>

          <!-- Phase connectors -->
          <g>
            <line
              v-for="(conn, i) in pipelineConnectors"
              :key="`pconn-${i}`"
              :x1="conn.x1"
              :y1="conn.y1"
              :x2="conn.x2"
              :y2="conn.y2"
              :stroke="conn.color"
              stroke-width="1.5"
              :marker-end="conn.marker"
            />
          </g>

          <!-- Phase nodes -->
          <g>
            <g
              v-for="phase in pipelinePhaseNodes"
              :key="phase.key"
              :transform="`translate(${phase.x}, ${phase.y})`"
              class="dep-node"
              @mouseenter="hoveredPhase = phase.key"
              @mouseleave="hoveredPhase = null"
            >
              <rect
                :width="PHASE_NODE_W"
                :height="PHASE_NODE_H"
                rx="6"
                :fill="phaseNodeFill(phase.status)"
                :stroke="phaseNodeStroke(phase.status)"
                stroke-width="1.5"
                :opacity="hoveredPhase === phase.key ? 1 : 0.9"
              />
              <!-- Status icon area -->
              <g :transform="`translate(${PHASE_NODE_W / 2}, 18)`">
                <!-- Completed checkmark -->
                <g v-if="phase.status === 'completed'">
                  <polyline
                    points="-5,0 -2,4 5,-4"
                    fill="none"
                    :stroke="COLORS.done"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  />
                </g>
                <!-- Failed X -->
                <g v-else-if="phase.status === 'failed'">
                  <line x1="-4" y1="-4" x2="4" y2="4" :stroke="COLORS.blocked" stroke-width="2" stroke-linecap="round" />
                  <line x1="4" y1="-4" x2="-4" y2="4" :stroke="COLORS.blocked" stroke-width="2" stroke-linecap="round" />
                </g>
                <!-- Active spinning dot -->
                <circle v-else-if="phase.status === 'active'" r="4" :fill="COLORS.active" opacity="0.9">
                  <animate attributeName="opacity" values="0.9;0.4;0.9" dur="1.5s" repeatCount="indefinite" />
                </circle>
                <!-- Pending empty circle -->
                <circle v-else r="4" fill="none" stroke="#374151" stroke-width="1.5" />
              </g>
              <!-- Phase label -->
              <text
                :x="PHASE_NODE_W / 2"
                y="35"
                text-anchor="middle"
                fill="#9ca3af"
                font-family="ui-monospace, 'Cascadia Code', Menlo, Consolas, monospace"
                font-size="8"
                font-weight="700"
                letter-spacing="0.06em"
              >{{ phase.label.toUpperCase() }}</text>
            </g>
          </g>
        </svg>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * DependencyGraph — SVG-based directed graph renderer for two modes:
 *   1. TASK DEPS:    shows tasks under a selected epic as nodes with dependency edges
 *   2. PIPELINE FLOW: shows the 8 PITER phases as a horizontal flow with status icons
 *
 * No external graph libraries — pure SVG + Vue reactivity.
 *
 * @task T054
 * @epic T051
 */
import { ref, computed, watch, onMounted } from "vue";
import { useCleoStore } from "../stores/cleoStore";
import { useOrchestratorStore } from "../stores/orchestratorStore";
import type { RunSummary } from "../types";

// ── Props ─────────────────────────────────────────────────────────────────────
const props = withDefaults(
  defineProps<{
    run?: RunSummary | null;
    epicId?: string | null;
  }>(),
  { run: null, epicId: null },
);

// ── Stores ────────────────────────────────────────────────────────────────────
const cleoStore = useCleoStore();
const orchStore = useOrchestratorStore();

// ── Constants ─────────────────────────────────────────────────────────────────
const NODE_W = 120;
const NODE_H = 36;
const WAVE_SPACING = 160;
const NODE_SPACING = 56;
const SVG_PAD_X = 16;
const SVG_PAD_Y = 16;

const PHASE_NODE_W = 72;
const PHASE_NODE_H = 44;
const PHASE_SPACING = 104;
const PHASE_PAD_X = 16;
const PHASE_PAD_Y = 16;

const COLORS = {
  done:    "#00ff66",
  active:  "#00ffcc",
  pending: "#444",
  blocked: "#ff4444",
  edge:    "#374151",
} as const;

// ── Mode ─────────────────────────────────────────────────────────────────────
const mode = ref<"task" | "pipeline">("pipeline");

// ── Run / epic selection ──────────────────────────────────────────────────────
const epicId = computed<string | null>(() => {
  return props.epicId ?? cleoStore.selectedEpicId ?? null;
});

const hasEpic = computed<boolean>(() => epicId.value !== null);

const selectedRun = computed<RunSummary | null>(() => {
  if (props.run) return props.run;
  const agent = orchStore.selectedAgent ?? orchStore.recentAgents[0] ?? null;
  if (!agent) return null;
  const meta = agent.metadata ?? {};
  return {
    adw_id: agent.id,
    repo_url: (meta.repo_url as string) ?? "",
    issue_number: (meta.issue_number as number) ?? 0,
    workflow: (agent.adw_step as string) ?? "",
    model_set: "",
    status: (meta.run_status as string) ?? agent.status,
    tokens_used: agent.input_tokens + agent.output_tokens,
  } as RunSummary;
});

const hasRun = computed<boolean>(() => selectedRun.value !== null);

// Auto-switch mode based on what's available
watch([hasEpic, hasRun], ([epic, run]) => {
  if (epic && mode.value === "pipeline" && !run) mode.value = "task";
  if (run && mode.value === "task" && !epic) mode.value = "pipeline";
});

// ── Task graph data ────────────────────────────────────────────────────────────
interface TaskWithDepends {
  id: string;
  title: string;
  status: string;
  depends: string[];
}

const taskGraphData = ref<TaskWithDepends[]>([]);
const taskLoading = ref(false);

async function fetchTasksWithDepends(parentId: string): Promise<void> {
  taskLoading.value = true;
  try {
    const resp = await fetch(`/api/cleo/tasks?parent=${encodeURIComponent(parentId)}&include_depends=1`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = (await resp.json()) as { tasks: TaskWithDepends[]; error?: string };
    taskGraphData.value = (data.tasks ?? []).map((t) => ({
      id: t.id,
      title: t.title ?? "",
      status: t.status ?? "pending",
      depends: Array.isArray(t.depends) ? t.depends : [],
    }));
  } catch (e) {
    console.error("[DependencyGraph] fetchTasksWithDepends:", e);
    // Fall back to store data if available
    const storeTasks = cleoStore.tasksByEpic[parentId];
    if (storeTasks) {
      taskGraphData.value = storeTasks.map((t) => ({
        id: t.id,
        title: t.title,
        status: t.status,
        depends: [],
      }));
    } else {
      taskGraphData.value = [];
    }
  } finally {
    taskLoading.value = false;
  }
}

watch(
  epicId,
  (id) => {
    taskGraphData.value = [];
    if (id) fetchTasksWithDepends(id);
  },
  { immediate: true },
);

// ── Graph layout ──────────────────────────────────────────────────────────────
interface GraphNode {
  id: string;
  title: string;
  status: string;
  normStatus: string;
  x: number;
  y: number;
}

interface GraphEdge {
  fromId: string;
  toId: string;
  fromNode: GraphNode;
  toNode: GraphNode;
}

function normaliseStatus(status: string): string {
  const s = (status ?? "").toLowerCase();
  if (s === "done" || s === "completed" || s === "succeeded") return "done";
  if (s === "active" || s === "in_progress" || s === "in-progress" || s === "running") return "active";
  if (s === "failed" || s === "blocked" || s === "cancelled" || s === "canceled") return "blocked";
  return "pending";
}

function layoutGraph(tasks: TaskWithDepends[]): { nodes: GraphNode[]; edges: GraphEdge[] } {
  if (tasks.length === 0) return { nodes: [], edges: [] };

  const idSet = new Set(tasks.map((t) => t.id));

  // Build dependency map: taskId → list of tasks it depends on (that exist in our set)
  const dependsOn: Map<string, Set<string>> = new Map();
  for (const t of tasks) {
    dependsOn.set(t.id, new Set(t.depends.filter((d) => idSet.has(d))));
  }

  // Topological sort into waves
  const wave: Map<string, number> = new Map();
  const assigned = new Set<string>();

  // Initial pass: tasks with no dependencies in our set go to wave 0
  for (const t of tasks) {
    const deps = dependsOn.get(t.id)!;
    if (deps.size === 0) {
      wave.set(t.id, 0);
      assigned.add(t.id);
    }
  }

  // If nothing got assigned (all tasks have circular or unknown deps), put everything in wave 0
  if (assigned.size === 0) {
    for (const t of tasks) {
      wave.set(t.id, 0);
      assigned.add(t.id);
    }
  }

  // Iteratively assign waves until all tasks are placed
  let changed = true;
  while (changed) {
    changed = false;
    for (const t of tasks) {
      if (assigned.has(t.id)) continue;
      const deps = dependsOn.get(t.id)!;
      // Check if all deps are assigned
      const depsWaves: number[] = [];
      let allAssigned = true;
      for (const dep of deps) {
        if (!assigned.has(dep)) { allAssigned = false; break; }
        depsWaves.push(wave.get(dep)!);
      }
      if (allAssigned) {
        const maxDepWave = depsWaves.length > 0 ? Math.max(...depsWaves) : 0;
        wave.set(t.id, maxDepWave + 1);
        assigned.add(t.id);
        changed = true;
      }
    }
  }

  // Any remaining unassigned (circular deps) — put at the end
  let maxWave = 0;
  for (const w of wave.values()) maxWave = Math.max(maxWave, w);
  for (const t of tasks) {
    if (!assigned.has(t.id)) {
      wave.set(t.id, maxWave + 1);
    }
  }

  // Group tasks by wave
  const waveGroups: Map<number, string[]> = new Map();
  for (const t of tasks) {
    const w = wave.get(t.id) ?? 0;
    if (!waveGroups.has(w)) waveGroups.set(w, []);
    waveGroups.get(w)!.push(t.id);
  }

  const sortedWaves = Array.from(waveGroups.keys()).sort((a, b) => a - b);
  const maxNodesInWave = Math.max(...Array.from(waveGroups.values()).map((g) => g.length));

  // Assign positions
  const nodeMap: Map<string, GraphNode> = new Map();
  const totalHeight = maxNodesInWave * NODE_SPACING + SVG_PAD_Y * 2;

  for (const [waveIdx, waveId] of sortedWaves.entries()) {
    const group = waveGroups.get(waveId)!;
    const groupHeight = group.length * NODE_SPACING;
    const startY = (totalHeight - groupHeight) / 2 + SVG_PAD_Y;

    for (const [nodeIdx, taskId] of group.entries()) {
      const task = tasks.find((t) => t.id === taskId)!;
      const x = SVG_PAD_X + waveIdx * WAVE_SPACING;
      const y = startY + nodeIdx * NODE_SPACING;
      const node: GraphNode = {
        id: task.id,
        title: task.title,
        status: task.status,
        normStatus: normaliseStatus(task.status),
        x,
        y,
      };
      nodeMap.set(task.id, node);
    }
  }

  const nodes = Array.from(nodeMap.values());

  // Build edges: for each task, draw edges FROM each dependency TO the task
  // (dependency → dependent, i.e. left to right)
  const edges: GraphEdge[] = [];
  for (const t of tasks) {
    const toNode = nodeMap.get(t.id);
    if (!toNode) continue;
    for (const dep of t.depends) {
      const fromNode = nodeMap.get(dep);
      if (!fromNode) continue;
      edges.push({ fromId: dep, toId: t.id, fromNode, toNode });
    }
  }

  return { nodes, edges };
}

const graphLayout = computed(() => layoutGraph(taskGraphData.value));
const graphNodes = computed(() => graphLayout.value.nodes);
const graphEdges = computed(() => graphLayout.value.edges);

const svgWidth = computed(() => {
  if (graphNodes.value.length === 0) return 200;
  return Math.max(...graphNodes.value.map((n) => n.x + NODE_W)) + SVG_PAD_X;
});

const svgHeight = computed(() => {
  if (graphNodes.value.length === 0) return 100;
  return Math.max(...graphNodes.value.map((n) => n.y + NODE_H)) + SVG_PAD_Y;
});

// ── Edge rendering ────────────────────────────────────────────────────────────
function edgePath(edge: GraphEdge): string {
  const x1 = edge.fromNode.x + NODE_W;
  const y1 = edge.fromNode.y + NODE_H / 2;
  const x2 = edge.toNode.x;
  const y2 = edge.toNode.y + NODE_H / 2;
  const cx = (x1 + x2) / 2;
  return `M ${x1} ${y1} C ${cx} ${y1}, ${cx} ${y2}, ${x2} ${y2}`;
}

function edgeStroke(edge: GraphEdge): string {
  const fromStatus = normaliseStatus(
    taskGraphData.value.find((t) => t.id === edge.fromId)?.status ?? "pending"
  );
  if (fromStatus === "done") return COLORS.done;
  if (fromStatus === "active") return COLORS.active;
  if (fromStatus === "blocked") return COLORS.blocked;
  return COLORS.edge;
}

function edgeMarker(edge: GraphEdge): string {
  const fromStatus = normaliseStatus(
    taskGraphData.value.find((t) => t.id === edge.fromId)?.status ?? "pending"
  );
  if (fromStatus === "done") return "url(#arrowhead-done)";
  if (fromStatus === "active") return "url(#arrowhead-active)";
  if (fromStatus === "blocked") return "url(#arrowhead-blocked)";
  return "url(#arrowhead)";
}

// ── Node styling ──────────────────────────────────────────────────────────────
function statusFill(normStatus: string): string {
  if (normStatus === "done")    return "rgba(0, 255, 102, 0.08)";
  if (normStatus === "active")  return "rgba(0, 255, 204, 0.08)";
  if (normStatus === "blocked") return "rgba(255, 68, 68, 0.08)";
  return "rgba(68, 68, 68, 0.2)";
}

function statusStroke(normStatus: string): string {
  if (normStatus === "done")    return COLORS.done;
  if (normStatus === "active")  return COLORS.active;
  if (normStatus === "blocked") return COLORS.blocked;
  return COLORS.pending;
}

// ── Tooltip ───────────────────────────────────────────────────────────────────
const hoveredNode = ref<string | null>(null);

const tooltipNode = computed<GraphNode | null>(() => {
  if (!hoveredNode.value) return null;
  return graphNodes.value.find((n) => n.id === hoveredNode.value) ?? null;
});

const tooltipWidth = 180;

const tooltipX = computed<number>(() => {
  if (!tooltipNode.value) return 0;
  const x = tooltipNode.value.x + NODE_W / 2 - tooltipWidth / 2;
  return Math.max(0, x);
});

const tooltipY = computed<number>(() => {
  if (!tooltipNode.value) return 0;
  return tooltipNode.value.y - 36;
});

// ── Interaction ───────────────────────────────────────────────────────────────
function openTask(taskId: string): void {
  cleoStore.openTaskModal(taskId);
}

// ── Utility ───────────────────────────────────────────────────────────────────
function truncate(s: string, len: number): string {
  if (s.length <= len) return s;
  return s.slice(0, len - 1) + "…";
}

// ── Pipeline phase flow ───────────────────────────────────────────────────────
const PITER_PHASES = [
  { key: "classify_iso", label: "Classify" },
  { key: "plan_iso",     label: "Plan" },
  { key: "build_iso",    label: "Build" },
  { key: "test_iso",     label: "Test" },
  { key: "review_iso",   label: "Review" },
  { key: "document_iso", label: "Document" },
  { key: "ship_iso",     label: "Ship" },
  { key: "reflect_iso",  label: "Reflect" },
] as const;

const phaseData = ref<Record<string, string>>({});
const hoveredPhase = ref<string | null>(null);

function normalisePhaseKey(raw: string): string {
  if (raw.endsWith("_iso")) return raw;
  return `${raw}_iso`;
}

async function fetchPhaseData(adwId: string): Promise<void> {
  try {
    const resp = await fetch(`/api/runs/${encodeURIComponent(adwId)}/phases`);
    if (!resp.ok) return;
    const data = (await resp.json()) as { phases: Array<{ phase: string; status: string }> };
    const map: Record<string, string> = {};
    for (const p of data.phases ?? []) {
      map[normalisePhaseKey(p.phase)] = p.status;
    }
    phaseData.value = map;
  } catch {
    // ignore — stale data acceptable
  }
}

watch(
  selectedRun,
  (run) => {
    phaseData.value = {};
    if (run?.adw_id) fetchPhaseData(run.adw_id);
  },
  { immediate: true },
);

// Derive active phase from event stream
const activePhaseKey = computed<string | null>(() => {
  if (!selectedRun.value) return null;
  const runId = selectedRun.value.adw_id;
  const events = orchStore.eventStreamEntries.filter(
    (e) => e.agentId === runId && e.metadata?.phase,
  );
  if (events.length === 0) return null;
  return normalisePhaseKey(String(events[events.length - 1].metadata!.phase));
});

type PhaseStatus = "completed" | "active" | "pending" | "failed";

function phaseStatusFor(key: string): PhaseStatus {
  const serverStatus = phaseData.value[key];
  if (serverStatus === "succeeded" || serverStatus === "completed") return "completed";
  if (serverStatus === "failed" || serverStatus === "aborted") return "failed";
  if (serverStatus === "running") return "active";

  const active = activePhaseKey.value;
  if (!active) return "pending";
  const phaseKeys = PITER_PHASES.map((p) => p.key as string);
  const activeIdx = phaseKeys.indexOf(active);
  const thisIdx = phaseKeys.indexOf(key);
  if (thisIdx < 0) return "pending";
  if (thisIdx < activeIdx) return "completed";
  if (thisIdx === activeIdx) return "active";
  return "pending";
}

interface PhaseNode {
  key: string;
  label: string;
  status: PhaseStatus;
  x: number;
  y: number;
}

const pipelinePhaseNodes = computed<PhaseNode[]>(() => {
  return PITER_PHASES.map((phase, i) => ({
    key: phase.key,
    label: phase.label,
    status: phaseStatusFor(phase.key),
    x: PHASE_PAD_X + i * PHASE_SPACING,
    y: PHASE_PAD_Y,
  }));
});

interface PipelineConnector {
  x1: number; y1: number;
  x2: number; y2: number;
  color: string;
  marker: string;
}

const pipelineConnectors = computed<PipelineConnector[]>(() => {
  const conns: PipelineConnector[] = [];
  const nodes = pipelinePhaseNodes.value;
  for (let i = 0; i < nodes.length - 1; i++) {
    const from = nodes[i];
    const to = nodes[i + 1];
    const x1 = from.x + PHASE_NODE_W;
    const y1 = from.y + PHASE_NODE_H / 2;
    const x2 = to.x;
    const y2 = to.y + PHASE_NODE_H / 2;
    let color = "#374151";
    let marker = "url(#pipe-arrow)";
    if (from.status === "completed") { color = COLORS.done; marker = "url(#pipe-arrow-done)"; }
    else if (from.status === "active") { color = COLORS.active; marker = "url(#pipe-arrow-active)"; }
    conns.push({ x1, y1, x2, y2, color, marker });
  }
  return conns;
});

const pipelineSvgWidth = computed(() => {
  return PHASE_PAD_X * 2 + PITER_PHASES.length * PHASE_SPACING;
});

const pipelineSvgHeight = computed(() => {
  return PHASE_PAD_Y * 2 + PHASE_NODE_H;
});

function phaseNodeFill(status: PhaseStatus): string {
  if (status === "completed") return "rgba(0, 255, 102, 0.08)";
  if (status === "active")    return "rgba(0, 255, 204, 0.1)";
  if (status === "failed")    return "rgba(255, 68, 68, 0.08)";
  return "rgba(30, 34, 41, 0.8)";
}

function phaseNodeStroke(status: PhaseStatus): string {
  if (status === "completed") return COLORS.done;
  if (status === "active")    return COLORS.active;
  if (status === "failed")    return COLORS.blocked;
  return "#374151";
}

// Ref for container (unused beyond SVG responsive scaling)
const svgContainerRef = ref<HTMLElement | null>(null);
</script>

<style scoped>
.dep-graph-wrap {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  min-height: 0;
}

/* ── Toolbar / mode toggle ──────────────────────────────────────────────────── */
.dep-graph-toolbar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  flex-shrink: 0;
}

.dep-mode-btn {
  background: none;
  border: none;
  color: #484f58;
  cursor: pointer;
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.1em;
  font-family: ui-monospace, "Cascadia Code", Menlo, Consolas, monospace;
  padding: 2px 6px;
  border-radius: 3px;
  transition: color 0.15s, background 0.15s;
}

.dep-mode-btn:hover:not(:disabled) {
  color: #8b949e;
  background: rgba(255, 255, 255, 0.04);
}

.dep-mode-btn.dep-mode-active {
  color: #00ffcc;
  background: rgba(0, 255, 204, 0.07);
}

.dep-mode-btn:disabled {
  opacity: 0.35;
  cursor: default;
}

.dep-mode-sep {
  color: #21262d;
  font-size: 12px;
  line-height: 1;
  user-select: none;
}

/* ── SVG area ────────────────────────────────────────────────────────────────── */
.dep-graph-svg-area {
  flex: 1;
  min-height: 0;
  display: flex;
  align-items: stretch;
  overflow: hidden;
}

.dep-graph-empty {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}

.dep-empty-icon {
  font-size: 16px;
  color: #374151;
}

.dep-empty-hint {
  font-size: 10px;
  color: #484f58;
  font-family: ui-monospace, "Cascadia Code", Menlo, Consolas, monospace;
}

/* ── SVG container (scrollable) ──────────────────────────────────────────────── */
.dep-svg-container {
  flex: 1;
  overflow: auto;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 8px;
}

.dep-svg-container::-webkit-scrollbar { width: 4px; height: 4px; }
.dep-svg-container::-webkit-scrollbar-track { background: transparent; }
.dep-svg-container::-webkit-scrollbar-thumb { background: rgba(255, 255, 255, 0.08); border-radius: 2px; }

.dep-svg {
  display: block;
  max-width: 100%;
  height: auto;
}

/* ── Node hover interaction ─────────────────────────────────────────────────── */
.dep-node {
  transition: opacity 0.15s;
}
.dep-node:hover rect {
  filter: brightness(1.15);
}
</style>
