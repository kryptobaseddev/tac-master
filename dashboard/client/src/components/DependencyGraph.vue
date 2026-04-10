<template>
  <div class="dep-graph-wrap">
    <!-- Header -->
    <div class="dep-graph-header">
      <span class="dep-graph-title">TASK DEPENDENCIES</span>
      <span v-if="epicId" class="dep-graph-epic-tag">{{ epicId }}</span>
      <span v-if="epicTitle" class="dep-graph-epic-title">{{ truncate(epicTitle, 40) }}</span>
    </div>

    <!-- Task dependency graph -->
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
          preserveAspectRatio="xMinYMin meet"
          class="dep-svg"
          xmlns="http://www.w3.org/2000/svg"
        >
          <defs>
            <!-- Arrowheads for dependency edges -->
            <marker id="arr-dep" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
              <polygon points="0 0, 8 3, 0 6" :fill="COLORS.edge" />
            </marker>
            <marker id="arr-done" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
              <polygon points="0 0, 8 3, 0 6" :fill="COLORS.done" />
            </marker>
            <marker id="arr-active" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
              <polygon points="0 0, 8 3, 0 6" :fill="COLORS.active" />
            </marker>
            <marker id="arr-blocked" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
              <polygon points="0 0, 8 3, 0 6" :fill="COLORS.blocked" />
            </marker>
            <!-- Glow filter for active nodes -->
            <filter id="glow-active" x="-30%" y="-30%" width="160%" height="160%">
              <feGaussianBlur stdDeviation="3" result="blur" />
              <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
            </filter>
          </defs>

          <!-- Parent edges: Epic → Task (thin gray lines behind nodes) -->
          <g class="dep-parent-edges">
            <line
              v-for="(edge, i) in epicEdges"
              :key="`epic-edge-${i}`"
              :x1="edge.x1"
              :y1="edge.y1"
              :x2="edge.x2"
              :y2="edge.y2"
              stroke="rgba(255,255,255,0.06)"
              stroke-width="1"
              stroke-dasharray="4 4"
            />
          </g>

          <!-- Dependency edges (Task→Task via depends) -->
          <g class="dep-edges">
            <path
              v-for="(edge, i) in graphEdges"
              :key="`edge-${i}`"
              :d="edgePath(edge)"
              fill="none"
              :stroke="edgeStroke(edge)"
              :stroke-width="edge.isBlocking ? 2 : 1.5"
              stroke-opacity="0.7"
              :stroke-dasharray="edge.isBlocking ? '5 3' : 'none'"
              :marker-end="edgeMarker(edge)"
            />
          </g>

          <!-- Epic node (Layer 0) -->
          <g
            v-if="epicNode"
            :transform="`translate(${epicNode.x}, ${epicNode.y})`"
            class="dep-node dep-node-epic"
            @click="openTask(epicNode.id)"
            @mouseenter="hoveredNode = epicNode.id"
            @mouseleave="hoveredNode = null"
            style="cursor: pointer;"
          >
            <rect
              :width="EPIC_W"
              :height="NODE_H"
              rx="6"
              fill="rgba(0,255,204,0.06)"
              stroke="#00ffcc"
              stroke-width="1.5"
              :opacity="hoveredNode === epicNode.id ? 1 : 0.8"
            />
            <!-- Epic label -->
            <text
              :x="EPIC_W / 2"
              y="16"
              text-anchor="middle"
              fill="#00ffcc"
              font-family="ui-monospace, 'Cascadia Code', Menlo, Consolas, monospace"
              font-size="10"
              font-weight="700"
              letter-spacing="0.04em"
            >{{ epicNode.id }}</text>
            <text
              :x="EPIC_W / 2"
              y="29"
              text-anchor="middle"
              fill="#9ca3af"
              font-family="ui-monospace, 'Cascadia Code', Menlo, Consolas, monospace"
              font-size="9"
            >{{ truncate(epicNode.title, 14) }}</text>
          </g>

          <!-- Task nodes (Layers 1+) -->
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
              <!-- Node background -->
              <rect
                :width="NODE_W"
                :height="NODE_H"
                rx="6"
                :fill="statusFill(node.normStatus)"
                :stroke="statusStroke(node.normStatus)"
                stroke-width="1.5"
                :opacity="hoveredNode === node.id ? 1 : 0.85"
                :filter="node.normStatus === 'active' ? 'url(#glow-active)' : ''"
              />

              <!-- Task ID row -->
              <text
                :x="NODE_W / 2"
                y="16"
                text-anchor="middle"
                :fill="statusStroke(node.normStatus)"
                font-family="ui-monospace, 'Cascadia Code', Menlo, Consolas, monospace"
                font-size="10"
                font-weight="700"
                letter-spacing="0.04em"
              >{{ node.id }}</text>

              <!-- Title row -->
              <text
                :x="NODE_W / 2"
                y="28"
                text-anchor="middle"
                fill="#9ca3af"
                font-family="ui-monospace, 'Cascadia Code', Menlo, Consolas, monospace"
                font-size="9"
              >{{ truncate(node.title, 19) }}</text>

              <!-- Status icon (top-right corner) -->
              <text
                :x="NODE_W - 8"
                y="13"
                text-anchor="middle"
                font-size="9"
                :fill="statusStroke(node.normStatus)"
              >{{ statusIcon(node.normStatus) }}</text>

              <!-- Progress bar (if node has children) -->
              <g v-if="node.progress !== null">
                <rect
                  :x="8"
                  :y="NODE_H - 9"
                  :width="NODE_W - 16"
                  height="4"
                  rx="2"
                  fill="rgba(255,255,255,0.06)"
                />
                <rect
                  :x="8"
                  :y="NODE_H - 9"
                  :width="Math.max(0, (NODE_W - 16) * node.progress)"
                  height="4"
                  rx="2"
                  :fill="statusStroke(node.normStatus)"
                  opacity="0.6"
                />
              </g>
            </g>
          </g>

          <!-- Tooltip overlay -->
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
 * DependencyGraph — SVG hierarchical tree + dependency edges renderer.
 *
 * Layout:
 *   Layer 0: Epic node (leftmost, EPIC_W wide)
 *   Layer 1: Tasks with no in-epic deps
 *   Layer 2+: Tasks that depend on earlier layers (topological sort)
 *
 * Edges:
 *   - Thin dashed gray: Epic → Task (hierarchy lines)
 *   - Colored arrows: Task → Task via depends[]
 *     Green if dependency is done, red dashed if blocking, gray otherwise
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
const EPIC_W     = 120;  // Epic node width
const NODE_W     = 160;  // Task node width
const NODE_H     = 50;   // Node height (enough for progress bar)
const LAYER_SPACING = 210; // Horizontal gap between layers
const ROW_HEIGHT    = 70;  // Vertical gap between nodes in same layer
const SVG_PAD_X     = 24;
const SVG_PAD_Y     = 24;
const EPIC_LAYER_W  = EPIC_W + 20; // space taken by epic column

const COLORS = {
  done:    "#00ff66",
  active:  "#00ffcc",
  pending: "#444",
  blocked: "#ff4444",
  edge:    "#374151",
} as const;

// ── Epic selection ────────────────────────────────────────────────────────────
const epicId = computed<string | null>(() => props.epicId ?? cleoStore.selectedEpicId ?? null);
const hasEpic = computed<boolean>(() => epicId.value !== null);

// ── Task graph data ────────────────────────────────────────────────────────────
interface TaskWithDepends {
  id: string;
  title: string;
  status: string;
  depends: string[];
  parent_id?: string | null;
  childCount?: number;
  doneCount?: number;
}

const taskGraphData = ref<TaskWithDepends[]>([]);
const epicTitle = ref<string>("");
const taskLoading = ref(false);

async function fetchTasksWithDepends(parentId: string): Promise<void> {
  taskLoading.value = true;
  epicTitle.value = "";
  try {
    const resp = await fetch(`/api/cleo/tasks?parent=${encodeURIComponent(parentId)}&include_depends=1`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = (await resp.json()) as {
      tasks: TaskWithDepends[];
      epic?: { title: string };
      error?: string;
    };
    taskGraphData.value = (data.tasks ?? []).map((t) => ({
      id: t.id,
      title: t.title ?? "",
      status: t.status ?? "pending",
      depends: Array.isArray(t.depends) ? t.depends : [],
      parent_id: t.parent_id ?? parentId,
      childCount: t.childCount ?? 0,
      doneCount: t.doneCount ?? 0,
    }));
    if (data.epic?.title) epicTitle.value = data.epic.title;
  } catch (e) {
    console.error("[DependencyGraph] fetchTasksWithDepends:", e);
    const storeTasks = cleoStore.tasksByEpic[parentId];
    if (storeTasks) {
      taskGraphData.value = storeTasks.map((t) => ({
        id: t.id,
        title: t.title,
        status: t.status,
        depends: [],
        parent_id: parentId,
        childCount: 0,
        doneCount: 0,
      }));
    } else {
      taskGraphData.value = [];
    }
  } finally {
    taskLoading.value = false;
  }
}

watch(epicId, (id) => {
  taskGraphData.value = [];
  if (id) fetchTasksWithDepends(id);
}, { immediate: true });

// ── Graph layout ──────────────────────────────────────────────────────────────
interface GraphNode {
  id: string;
  title: string;
  status: string;
  normStatus: string;
  x: number;
  y: number;
  progress: number | null;
}

interface GraphEdge {
  fromId: string;
  toId: string;
  fromNode: GraphNode;
  toNode: GraphNode;
  isBlocking: boolean;
}

interface EpicEdge {
  x1: number; y1: number;
  x2: number; y2: number;
}

function normaliseStatus(status: string): string {
  const s = (status ?? "").toLowerCase();
  if (s === "done" || s === "completed" || s === "succeeded") return "done";
  if (s === "active" || s === "in_progress" || s === "in-progress" || s === "running") return "active";
  if (s === "failed" || s === "blocked" || s === "cancelled" || s === "canceled") return "blocked";
  return "pending";
}

function layoutGraph(tasks: TaskWithDepends[]): {
  nodes: GraphNode[];
  edges: GraphEdge[];
  epicEdges: EpicEdge[];
} {
  if (tasks.length === 0) return { nodes: [], edges: [], epicEdges: [] };

  const idSet = new Set(tasks.map((t) => t.id));

  // Build dependency map: taskId → tasks it depends on (within our set)
  const dependsOn = new Map<string, Set<string>>();
  for (const t of tasks) {
    dependsOn.set(t.id, new Set(t.depends.filter((d) => idSet.has(d))));
  }

  // Topological sort into waves
  const wave = new Map<string, number>();
  const assigned = new Set<string>();

  // Wave 0: tasks with no in-set dependencies
  for (const t of tasks) {
    if (dependsOn.get(t.id)!.size === 0) {
      wave.set(t.id, 0);
      assigned.add(t.id);
    }
  }

  if (assigned.size === 0) {
    for (const t of tasks) { wave.set(t.id, 0); assigned.add(t.id); }
  }

  // Iteratively assign waves
  let changed = true;
  while (changed) {
    changed = false;
    for (const t of tasks) {
      if (assigned.has(t.id)) continue;
      const deps = dependsOn.get(t.id)!;
      const depsWaves: number[] = [];
      let allAssigned = true;
      for (const dep of deps) {
        if (!assigned.has(dep)) { allAssigned = false; break; }
        depsWaves.push(wave.get(dep)!);
      }
      if (allAssigned) {
        wave.set(t.id, (depsWaves.length > 0 ? Math.max(...depsWaves) : 0) + 1);
        assigned.add(t.id);
        changed = true;
      }
    }
  }

  // Unassigned (circular) → last wave
  let maxWave = 0;
  for (const w of wave.values()) maxWave = Math.max(maxWave, w);
  for (const t of tasks) {
    if (!assigned.has(t.id)) wave.set(t.id, maxWave + 1);
  }

  // Group by wave
  const waveGroups = new Map<number, string[]>();
  for (const t of tasks) {
    const w = wave.get(t.id) ?? 0;
    if (!waveGroups.has(w)) waveGroups.set(w, []);
    waveGroups.get(w)!.push(t.id);
  }

  const sortedWaves = Array.from(waveGroups.keys()).sort((a, b) => a - b);
  const maxNodesInWave = Math.max(...Array.from(waveGroups.values()).map((g) => g.length));

  // Layer 0 is the epic — task layers start at layer 1 (offset by EPIC_LAYER_W)
  const totalHeight = maxNodesInWave * ROW_HEIGHT + SVG_PAD_Y * 2;

  const nodeMap = new Map<string, GraphNode>();
  for (const [waveIdx, waveId] of sortedWaves.entries()) {
    const group = waveGroups.get(waveId)!;
    const groupHeight = group.length * ROW_HEIGHT;
    const startY = (totalHeight - groupHeight) / 2 + SVG_PAD_Y;

    for (const [nodeIdx, taskId] of group.entries()) {
      const task = tasks.find((t) => t.id === taskId)!;
      // x: skip epic column (EPIC_LAYER_W), then space layers
      const x = SVG_PAD_X + EPIC_LAYER_W + waveIdx * LAYER_SPACING;
      const y = startY + nodeIdx * ROW_HEIGHT;

      // Progress: 0..1 if childCount > 0
      const childCount = task.childCount ?? 0;
      const doneCount = task.doneCount ?? 0;
      const progress = childCount > 0 ? doneCount / childCount : null;

      nodeMap.set(taskId, {
        id: task.id,
        title: task.title,
        status: task.status,
        normStatus: normaliseStatus(task.status),
        x,
        y,
        progress,
      });
    }
  }

  const nodes = Array.from(nodeMap.values());

  // Dependency edges
  const doneSt = new Set(["done", "completed", "succeeded"]);
  const edges: GraphEdge[] = [];
  for (const t of tasks) {
    const toNode = nodeMap.get(t.id);
    if (!toNode) continue;
    for (const dep of t.depends) {
      const fromNode = nodeMap.get(dep);
      if (!fromNode) continue;
      const depTask = tasks.find((x) => x.id === dep);
      const depNorm = normaliseStatus(depTask?.status ?? "pending");
      const isBlocking = depNorm !== "done" && normaliseStatus(t.status) === "pending";
      edges.push({ fromId: dep, toId: t.id, fromNode, toNode, isBlocking });
    }
  }

  return { nodes, edges, epicEdges: [] };
}

const graphLayout = computed(() => layoutGraph(taskGraphData.value));
const graphNodes = computed(() => graphLayout.value.nodes);
const graphEdges = computed(() => graphLayout.value.edges);

// ── Epic node ────────────────────────────────────────────────────────────────
const epicNode = computed<{ id: string; title: string; x: number; y: number } | null>(() => {
  if (!epicId.value || graphNodes.value.length === 0) return null;
  const nodes = graphNodes.value;
  const minY = Math.min(...nodes.map((n) => n.y));
  const maxY = Math.max(...nodes.map((n) => n.y + NODE_H));
  const centerY = (minY + maxY) / 2 - NODE_H / 2;
  return {
    id: epicId.value,
    title: epicTitle.value,
    x: SVG_PAD_X,
    y: centerY,
  };
});

// Epic → Task lines (hierarchy)
const epicEdges = computed<EpicEdge[]>(() => {
  if (!epicNode.value) return [];
  const epicRight = epicNode.value.x + EPIC_W;
  const epicMidY = epicNode.value.y + NODE_H / 2;
  return graphNodes.value.map((n) => ({
    x1: epicRight,
    y1: epicMidY,
    x2: n.x,
    y2: n.y + NODE_H / 2,
  }));
});

// ── SVG dimensions ────────────────────────────────────────────────────────────
const svgWidth = computed(() => {
  if (graphNodes.value.length === 0) return 400;
  return Math.max(...graphNodes.value.map((n) => n.x + NODE_W)) + SVG_PAD_X;
});

const svgHeight = computed(() => {
  if (graphNodes.value.length === 0) return 280;
  const natural = Math.max(...graphNodes.value.map((n) => n.y + NODE_H)) + SVG_PAD_Y;
  return Math.max(natural, 280);
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
  if (edge.isBlocking)  return COLORS.blocked;
  if (fromStatus === "done")    return COLORS.done;
  if (fromStatus === "active")  return COLORS.active;
  if (fromStatus === "blocked") return COLORS.blocked;
  return COLORS.edge;
}

function edgeMarker(edge: GraphEdge): string {
  if (edge.isBlocking) return "url(#arr-blocked)";
  const fromStatus = normaliseStatus(
    taskGraphData.value.find((t) => t.id === edge.fromId)?.status ?? "pending"
  );
  if (fromStatus === "done")    return "url(#arr-done)";
  if (fromStatus === "active")  return "url(#arr-active)";
  if (fromStatus === "blocked") return "url(#arr-blocked)";
  return "url(#arr-dep)";
}

// ── Node styling ──────────────────────────────────────────────────────────────
function statusFill(normStatus: string): string {
  if (normStatus === "done")    return "rgba(0, 255, 102, 0.08)";
  if (normStatus === "active")  return "rgba(0, 255, 204, 0.10)";
  if (normStatus === "blocked") return "rgba(255, 68, 68, 0.10)";
  return "rgba(68, 68, 68, 0.18)";
}

function statusStroke(normStatus: string): string {
  if (normStatus === "done")    return COLORS.done;
  if (normStatus === "active")  return COLORS.active;
  if (normStatus === "blocked") return COLORS.blocked;
  return COLORS.pending;
}

function statusIcon(normStatus: string): string {
  if (normStatus === "done")    return "✓";
  if (normStatus === "active")  return "▶";
  if (normStatus === "blocked") return "✗";
  return "○";
}

// ── Tooltip ───────────────────────────────────────────────────────────────────
const hoveredNode = ref<string | null>(null);

const tooltipNode = computed<GraphNode | { id: string; title: string; x: number; y: number } | null>(() => {
  if (!hoveredNode.value) return null;
  if (epicNode.value && hoveredNode.value === epicNode.value.id) return epicNode.value;
  return graphNodes.value.find((n) => n.id === hoveredNode.value) ?? null;
});

const tooltipWidth = 200;

const tooltipX = computed<number>(() => {
  if (!tooltipNode.value) return 0;
  const nodeW = hoveredNode.value === epicNode.value?.id ? EPIC_W : NODE_W;
  const x = tooltipNode.value.x + nodeW / 2 - tooltipWidth / 2;
  return Math.max(0, x);
});

const tooltipY = computed<number>(() => {
  if (!tooltipNode.value) return 0;
  return Math.max(0, tooltipNode.value.y - 36);
});

// ── Interaction ───────────────────────────────────────────────────────────────
function openTask(taskId: string): void {
  cleoStore.openTaskModal(taskId);
}

// ── Utility ───────────────────────────────────────────────────────────────────
function truncate(s: string, len: number): string {
  if (!s || s.length <= len) return s;
  return s.slice(0, len - 1) + "…";
}

const svgContainerRef = ref<HTMLElement | null>(null);
</script>

<style scoped>
.dep-graph-wrap {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  min-height: 350px;
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
  flex-shrink: 0;
}

.dep-graph-epic-title {
  font-size: 9px;
  color: #6b7280;
  font-family: ui-monospace, "Cascadia Code", Menlo, Consolas, monospace;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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

/* ── SVG container (scrollable both axes) ──────────────────────────────────── */
.dep-svg-container {
  flex: 1;
  width: 100%;
  overflow-x: auto;
  overflow-y: auto;
  display: flex;
  align-items: flex-start;
  justify-content: flex-start;
  padding: 8px;
  box-sizing: border-box;
}

.dep-svg-container::-webkit-scrollbar { width: 4px; height: 4px; }
.dep-svg-container::-webkit-scrollbar-track { background: transparent; }
.dep-svg-container::-webkit-scrollbar-thumb { background: rgba(255, 255, 255, 0.08); border-radius: 2px; }

.dep-svg {
  display: block;
  flex-shrink: 0;
}

/* ── Node hover ─────────────────────────────────────────────────────────────── */
.dep-node {
  transition: opacity 0.15s;
}
.dep-node:hover rect {
  filter: brightness(1.2);
}
</style>
