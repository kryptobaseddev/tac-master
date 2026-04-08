import { ref, onBeforeUnmount } from "vue";
import type {
  HookEvent,
  RepoStatus,
  RunSummary,
  WsMessage,
} from "../types";

const MAX_EVENTS = Number(import.meta.env.VITE_MAX_EVENTS ?? 500);

export function useWebSocket(url: string) {
  const events = ref<HookEvent[]>([]);
  const runs = ref<Map<string, RunSummary>>(new Map());
  const repos = ref<Map<string, RepoStatus>>(new Map());
  const isConnected = ref(false);
  const error = ref<string | null>(null);

  let ws: WebSocket | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  function connect(): void {
    try {
      ws = new WebSocket(url);
    } catch (e: any) {
      error.value = String(e?.message ?? e);
      scheduleReconnect();
      return;
    }

    ws.onopen = () => {
      isConnected.value = true;
      error.value = null;
    };

    ws.onmessage = (msg) => {
      let parsed: WsMessage;
      try {
        parsed = JSON.parse(msg.data);
      } catch {
        return;
      }
      handleMessage(parsed);
    };

    ws.onerror = () => {
      error.value = "websocket error";
    };

    ws.onclose = () => {
      isConnected.value = false;
      scheduleReconnect();
    };
  }

  function handleMessage(msg: WsMessage): void {
    switch (msg.type) {
      case "initial":
        events.value = msg.data.slice(-MAX_EVENTS);
        break;
      case "event":
        events.value.push(msg.data);
        if (events.value.length > MAX_EVENTS) {
          events.value.splice(0, events.value.length - MAX_EVENTS);
        }
        break;
      case "run_update":
        runs.value.set(msg.data.adw_id, msg.data);
        // Trigger reactivity
        runs.value = new Map(runs.value);
        break;
      case "repo_status":
        repos.value.set(msg.data.url, msg.data);
        repos.value = new Map(repos.value);
        break;
    }
  }

  function scheduleReconnect(): void {
    if (reconnectTimer) return;
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connect();
    }, 3000);
  }

  connect();

  onBeforeUnmount(() => {
    if (reconnectTimer) clearTimeout(reconnectTimer);
    ws?.close();
  });

  return { events, runs, repos, isConnected, error };
}
