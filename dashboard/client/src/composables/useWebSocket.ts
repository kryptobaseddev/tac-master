/**
 * useWebSocket — WebSocket composable for tac-master dashboard.
 *
 * Satisfies T071 and T092 (merged):
 *   - Connects to ws://HOST:PORT/stream on mount
 *   - Parses incoming WsMessage by type and dispatches to Pinia store actions
 *   - Exponential backoff reconnect: 1s → 2s → 4s → 8s → 16s → 30s (capped)
 *   - After reconnect: calls GET /events/recent and dispatches each event
 *   - Exposes: isConnected, reconnectCount, connectionStatus, disconnect()
 *
 * @task T071, T092
 * @epic T058
 */

import { ref, onMounted, onUnmounted } from "vue";
import { useOrchestratorStore } from "../stores/orchestratorStore";
import type { WsMessage } from "../types/ws-events";

// Connection status values
export type ConnectionStatus = "connected" | "reconnecting" | "disconnected";

// Backoff config
const BACKOFF_STEPS_MS = [1000, 2000, 4000, 8000, 16000, 30000];
const BACKOFF_MAX_MS = 30000;

/**
 * Build the WebSocket URL from the current browser origin.
 * Uses ws:// for http:// origins and wss:// for https:// origins.
 * In Vite dev mode the proxy forwards /stream to the backend so
 * window.location.host works for both dev and production.
 */
function buildWsUrl(): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const host = window.location.host; // includes port if non-standard
  return `${proto}//${host}/stream`;
}

export function useWebSocket() {
  const store = useOrchestratorStore();

  const isConnected = ref<boolean>(false);
  const reconnectCount = ref<number>(0);
  const connectionStatus = ref<ConnectionStatus>("disconnected");

  let ws: WebSocket | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let destroyed = false; // set true on manual disconnect / unmount

  // ── Event routing ───────────────────────────────────────────────

  function routeMessage(msg: WsMessage): void {
    switch (msg.type) {
      case "initial":
        // Hydrate the event stream from the initial batch
        store.hydrateFromInitial(msg.data);
        break;

      case "event":
        // Legacy hook event — also route to swimlanes if it has adw_id (T134)
        store.addHookEvent(msg.data);
        if (msg.data.adw_id && typeof (store as any).handleAdwWsEvent === "function") {
          (store as any).handleAdwWsEvent(msg.data);
        }
        break;

      case "run_update":
        store.upsertRun(msg.data);
        break;

      case "repo_status":
        store.upsertRepo(msg.data);
        break;

      case "thinking_block":
        // T134: Route to handleAdwWsEvent for swimlane real-time updates
        if (typeof (store as any).handleAdwWsEvent === "function") {
          (store as any).handleAdwWsEvent(msg);
        }
        // Also call addThinkingBlock if it exists (for streaming blocks panel)
        if (typeof (store as any).addThinkingBlock === "function") {
          (store as any).addThinkingBlock(msg.data);
        }
        break;

      case "tool_use_block":
        // T134: Route to handleAdwWsEvent for swimlane real-time updates
        if (typeof (store as any).handleAdwWsEvent === "function") {
          (store as any).handleAdwWsEvent(msg);
        }
        // Also call addToolUseBlock if it exists (for streaming blocks panel)
        if (typeof (store as any).addToolUseBlock === "function") {
          (store as any).addToolUseBlock(msg.data);
        }
        break;

      case "text_block":
        // T134: Route to handleAdwWsEvent for swimlane real-time updates
        if (typeof (store as any).handleAdwWsEvent === "function") {
          (store as any).handleAdwWsEvent(msg);
        }
        // Also call addTextBlock if it exists (for streaming blocks panel)
        if (typeof (store as any).addTextBlock === "function") {
          (store as any).addTextBlock(msg.data);
        }
        break;

      case "agent_status":
        // T134: Route to handleAdwWsEvent for swimlane real-time updates
        if (typeof (store as any).handleAdwWsEvent === "function") {
          (store as any).handleAdwWsEvent(msg);
        }
        // Also call addAgentStatus if it exists (for status tracking)
        if (typeof (store as any).addAgentStatus === "function") {
          (store as any).addAgentStatus(msg.data);
        }
        break;

      case "heartbeat":
        // TODO (T102): implement store.updateHeartbeat(msg.data)
        if (typeof (store as any).updateHeartbeat === "function") {
          (store as any).updateHeartbeat(msg.data);
        }
        // Heartbeat is informational — no fallback needed
        break;

      default:
        // Exhaustiveness guard — unexpected message type
        console.warn("[useWebSocket] unknown message type:", (msg as any).type);
    }
  }

  // ── POST-reconnect hydration ────────────────────────────────────

  async function hydrateRecentEvents(): Promise<void> {
    try {
      const resp = await fetch("/events/recent?limit=200");
      if (!resp.ok) return;
      const events: unknown = await resp.json();
      if (Array.isArray(events)) {
        for (const event of events) {
          store.addHookEvent(event);
        }
      }
    } catch (err) {
      console.warn("[useWebSocket] hydrateRecentEvents failed:", err);
    }
  }

  // ── Backoff helpers ─────────────────────────────────────────────

  function backoffDelay(attempt: number): number {
    const idx = Math.min(attempt, BACKOFF_STEPS_MS.length - 1);
    return BACKOFF_STEPS_MS[idx] ?? BACKOFF_MAX_MS;
  }

  // ── WebSocket lifecycle ─────────────────────────────────────────

  function connect(isReconnect = false): void {
    if (destroyed) return;

    const url = buildWsUrl();

    if (isReconnect) {
      connectionStatus.value = "reconnecting";
    } else {
      connectionStatus.value = "disconnected";
    }

    ws = new WebSocket(url);

    ws.onopen = () => {
      if (destroyed) {
        ws?.close();
        return;
      }
      isConnected.value = true;
      connectionStatus.value = "connected";

      if (isReconnect) {
        // Hydrate missed events after a reconnect
        hydrateRecentEvents();
      }
    };

    ws.onmessage = (evt: MessageEvent) => {
      try {
        const msg = JSON.parse(evt.data as string) as WsMessage;
        routeMessage(msg);
      } catch (err) {
        console.warn("[useWebSocket] message parse error:", err);
      }
    };

    ws.onerror = (evt) => {
      console.warn("[useWebSocket] socket error:", evt);
      // onclose will fire next and handle reconnect
    };

    ws.onclose = () => {
      isConnected.value = false;
      ws = null;

      if (destroyed) {
        connectionStatus.value = "disconnected";
        return;
      }

      connectionStatus.value = "reconnecting";
      const delay = backoffDelay(reconnectCount.value);
      reconnectCount.value += 1;

      reconnectTimer = setTimeout(() => {
        connect(true /* isReconnect */);
      }, delay);
    };
  }

  // ── Public API ──────────────────────────────────────────────────

  function disconnect(): void {
    destroyed = true;
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    ws?.close();
    ws = null;
    isConnected.value = false;
    connectionStatus.value = "disconnected";
  }

  // ── Vue lifecycle hooks ─────────────────────────────────────────

  onMounted(() => {
    connect(false);
  });

  onUnmounted(() => {
    disconnect();
  });

  return {
    /** True while the socket is open and healthy */
    isConnected,
    /** Number of reconnect attempts since the composable was mounted */
    reconnectCount,
    /** "connected" | "reconnecting" | "disconnected" */
    connectionStatus,
    /** Permanently close the socket and cancel any pending reconnect */
    disconnect,
  };
}
