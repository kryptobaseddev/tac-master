/**
 * Pinia store for CLEO Epic & Task Tree data.
 *
 * Fetches from /api/cleo/epics (and lazily /api/cleo/tasks?parent=X) and
 * polls every 30 seconds to keep the panel fresh without requiring a page
 * reload.
 *
 * @task T040
 * @epic T036
 */

import { defineStore } from "pinia";
import { ref, computed } from "vue";

// ---------------------------------------------------------------------------
// Types (mirroring cleo-api.ts server shapes)
// ---------------------------------------------------------------------------

export interface EpicProgress {
  total: number;
  done: number;
  active: number;
  pending: number;
  failed: number;
}

export interface EpicSummary {
  id: string;
  title: string;
  status: string;
  priority: string;
  size: string | null;
  labels: string[];
  progress: EpicProgress;
  pct: number; // 0-100
}

export interface TaskSummary {
  id: string;
  title: string;
  status: string;
  type: string | null;
  priority: string;
  size: string | null;
  parent_id: string | null;
  labels: string[];
  acceptance: string[];
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export const useCleoStore = defineStore("cleo", () => {
  // --- State ---
  const epics = ref<EpicSummary[]>([]);
  const tasksByEpic = ref<Record<string, TaskSummary[]>>({});
  const selectedEpicId = ref<string | null>(null);
  const loading = ref(false);
  const error = ref<string | null>(null);
  const dbPath = ref<string | null>(null);
  const lastFetched = ref<Date | null>(null);

  let _pollTimer: ReturnType<typeof setInterval> | null = null;

  // --- Getters ---

  const selectedEpic = computed<EpicSummary | null>(
    () => epics.value.find((e) => e.id === selectedEpicId.value) ?? null,
  );

  /** True if any epic has an active child task — used to highlight the active epic */
  const activeEpicId = computed<string | null>(() => {
    for (const epic of epics.value) {
      if (epic.progress.active > 0) return epic.id;
    }
    return null;
  });

  // --- Actions ---

  async function fetchEpics(): Promise<void> {
    loading.value = true;
    error.value = null;
    try {
      const resp = await fetch("/api/cleo/epics");
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = (await resp.json()) as {
        epics: EpicSummary[];
        dbPath: string | null;
        error?: string;
      };
      epics.value = data.epics ?? [];
      dbPath.value = data.dbPath ?? null;
      if (data.error) error.value = data.error;
      lastFetched.value = new Date();
    } catch (e: unknown) {
      error.value = e instanceof Error ? e.message : String(e);
    } finally {
      loading.value = false;
    }
  }

  async function fetchTasks(epicId: string): Promise<void> {
    try {
      const resp = await fetch(`/api/cleo/tasks?parent=${encodeURIComponent(epicId)}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = (await resp.json()) as { tasks: TaskSummary[]; error?: string };
      tasksByEpic.value = { ...tasksByEpic.value, [epicId]: data.tasks ?? [] };
    } catch (e: unknown) {
      console.error("[cleoStore] fetchTasks error:", e);
    }
  }

  function selectEpic(id: string | null): void {
    selectedEpicId.value = id;
    if (id && !tasksByEpic.value[id]) {
      fetchTasks(id);
    }
  }

  function startPolling(intervalMs = 30_000): void {
    stopPolling();
    _pollTimer = setInterval(() => {
      fetchEpics();
      // Refresh any expanded epic's tasks too
      if (selectedEpicId.value) fetchTasks(selectedEpicId.value);
    }, intervalMs);
  }

  function stopPolling(): void {
    if (_pollTimer !== null) {
      clearInterval(_pollTimer);
      _pollTimer = null;
    }
  }

  async function initialize(): Promise<void> {
    await fetchEpics();
    startPolling();
  }

  return {
    // state
    epics,
    tasksByEpic,
    selectedEpicId,
    loading,
    error,
    dbPath,
    lastFetched,
    // getters
    selectedEpic,
    activeEpicId,
    // actions
    fetchEpics,
    fetchTasks,
    selectEpic,
    startPolling,
    stopPolling,
    initialize,
  };
});
