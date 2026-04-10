<template>
  <div class="dep-graph-wrap">
    <!-- Header -->
    <div class="dep-graph-header">
      <span class="dep-graph-title">TASK DEPENDENCIES</span>
      <span v-if="epicId" class="dep-graph-epic-tag">{{ epicId }}</span>
    </div>

    <!-- Task dependency graph (always task-deps mode) -->
    <div class="dep-graph-svg-area">
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
          preserveAspectRatio="xMidYMid meet"
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
  </div>
</template>

<script setup lang="ts">
/**
 * DependencyGraph — SVG-based directed graph renderer for task dependencies.
 *
 * Shows tasks under a selected epic as nodes with dependency edges.
 * Layout left-to-right by wave (Wave 0 leftmost, Wave N rightmost).
 * Node color by status (green=done, cyan=active, gray=pending, red=blocked).
 * Clicking a node opens TaskDetailModal.
 *
 * The selected epic is driven by cleoStore.selectedEpicId (set when user
 * expands an epic in EpicTaskTree) or the epicId prop.
 *
 * Pipeline Flow mode has been removed — PipelineFlow.vue handles phase
 * visualization exclusively.
 *
 * @task T054
 * @epic T051
 */
import { ref, computed, watch } from "vue";
import { useCleoStore } from "../stores/cleoStore";

// ── Props ─────────────────────────────────────────────────────────────────────
const props = withDefaults(
  defineProps<{
    epicId?: string | null;
  }>(),
  { epicId: null },
);

// ── Stores ────────────────────────────────────────────────────────────────────
const cleoStore = useCleoStore();

// ── Constants ─────────────────────────────────────────────────────────────────
const NODE_W = 120;
const NODE_H = 36;
const WAVE_SPACING = 180;
const NODE_SPACING = 70;
const SVG_PAD_X = 20;
const SVG_PAD_Y = 20;

const COLORS = {
  done:    "#00ff66",
  active:  "#00ffcc",
  pending: "#444",
  blocked: "#ff4444",
  edge:    "#374151",
} as const;

// ── Epic selection ────────────────────────────────────────────────────────────
// Prop takes precedence; falls back to store's selected epic (set by EpicTaskTree)
const epicId = computed<string | null>(() => {
  return props.epicId ?? cleoStore.selectedEpicId ?? null;
});

const hasEpic = computed<boolean>(() => epicId.value !== null);

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
  if (graphNodes.value.length === 0) return 300;
  return Math.max(...graphNodes.value.map((n) => n.x + NODE_W)) + SVG_PAD_X;
});

const svgHeight = computed(() => {
  if (graphNodes.value.length === 0) return 200;
  const natural = Math.max(...graphNodes.value.map((n) => n.y + NODE_H)) + SVG_PAD_Y;
  return Math.max(natural, 200);
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

// Ref for container
const svgContainerRef = ref<HTMLElement | null>(null);
</script>

<style scoped>
.dep-graph-wrap {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  min-height: 300px;
}

/* ── Header ──────────────────────────────────────────────────────────────────── */
.dep-graph-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 10px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  flex-shrink: 0;
}

.dep-graph-title {
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.1em;
  color: #484f58;
  font-family: ui-monospace, "Cascadia Code", Menlo, Consolas, monospace;
  text-transform: uppercase;
}

.dep-graph-epic-tag {
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.06em;
  color: #00ffcc;
  font-family: ui-monospace, "Cascadia Code", Menlo, Consolas, monospace;
  background: rgba(0, 255, 204, 0.07);
  padding: 1px 5px;
  border-radius: 3px;
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

/* ── SVG container (horizontally scrollable) ──────────────────────────────────── */
.dep-svg-container {
  flex: 1;
  overflow-x: auto;
  overflow-y: auto;
  display: flex;
  align-items: flex-start;
  justify-content: flex-start;
  padding: 8px;
}

.dep-svg-container::-webkit-scrollbar { width: 4px; height: 4px; }
.dep-svg-container::-webkit-scrollbar-track { background: transparent; }
.dep-svg-container::-webkit-scrollbar-thumb { background: rgba(255, 255, 255, 0.08); border-radius: 2px; }

.dep-svg {
  display: block;
  flex-shrink: 0;
}

/* ── Node hover interaction ─────────────────────────────────────────────────── */
.dep-node {
  transition: opacity 0.15s;
}
.dep-node:hover rect {
  filter: brightness(1.15);
}
</style>
