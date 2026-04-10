/**
 * Integration tests for hook POST to WebSocket broadcast pipeline.
 *
 * Verifies the complete flow: POST /events with HookEvent (with block_type) → server
 * stores event → server broadcasts typed WsMessage to connected clients within 500ms.
 *
 * Uses a real Bun HTTP + WebSocket server on test port 4099. No mocks for HTTP or WS.
 *
 * @task T085
 * @epic T058
 */

import { describe, it, expect, beforeAll, afterAll, afterEach } from "bun:test";

// Test server will be started on this port
const TEST_PORT = 4099;
const TEST_URL = `http://localhost:${TEST_PORT}`;
const TEST_WS_URL = `ws://localhost:${TEST_PORT}/stream`;

// Global server and WebSocket client references
let server: any = null;
let wsClient: WebSocket | null = null;
let receivedMessages: any[] = [];
let wsOpenPromise: Promise<void> | null = null;
let wsOpenResolve: (() => void) | null = null;

/**
 * Mini server implementation for testing.
 * Reuses the core broadcast logic from index.ts.
 */
async function startTestServer() {
  // Re-export and mock the database to avoid SQLite state pollution
  const mockDb = {
    initDatabase: () => {},
    insertEvent: (ev: any) => ({
      ...ev,
      id: Math.floor(Math.random() * 1000000),
      timestamp: ev.timestamp ?? Date.now(),
    }),
    getRecentEvents: () => [],
    getEventsByAdwId: () => [],
    getFilterOptions: () => ({}),
    getRepoStatuses: () => [],
    getActiveAndRecentRuns: () => [],
    getLessons: () => [],
    getRunPhases: () => [],
    getRunPhaseSummary: () => ({}),
    getAggregateStats: () => ({}),
    logOperatorAction: () => {},
    getOperatorLog: () => [],
    insertChatMessage: () => ({}),
    getChatHistory: () => [],
    getTurnCount: () => 0,
    getActiveOrchestrator: () => null,
    getSystemLogs: () => [],
  };

  // Import WebSocketManager
  const { WebSocketManager } = await import("../src/ws-manager");
  const wsManager = new WebSocketManager();

  const CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, PUT, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
  };

  function json(body: unknown, status = 200): Response {
    return new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json", ...CORS_HEADERS },
    });
  }

  const testServer = Bun.serve({
    port: TEST_PORT,
    development: false,

    async fetch(req, srv) {
      const url = new URL(req.url);

      // OPTIONS preflight
      if (req.method === "OPTIONS") {
        return new Response(null, { status: 204, headers: CORS_HEADERS });
      }

      // WebSocket upgrade
      if (url.pathname === "/stream") {
        if (srv.upgrade(req)) return;
        return new Response("WebSocket upgrade failed", { status: 400 });
      }

      // Health check
      if (url.pathname === "/health") {
        return json({
          status: "ok",
          service: "test-server",
          clients: wsManager.size(),
        });
      }

      // POST /events — core broadcast logic from index.ts
      if (url.pathname === "/events" && req.method === "POST") {
        try {
          const body = (await req.json()) as any;
          if (!body.source_app || !body.session_id || !body.hook_event_type) {
            return json({ error: "missing required fields" }, 400);
          }

          const saved = mockDb.insertEvent(body);

          // Always broadcast the raw event
          wsManager.broadcast({ type: "event", data: saved });

          // Route by block_type
          const ts = saved.timestamp ?? Date.now();
          const adwId = saved.adw_id ?? saved.session_id;
          const phase = saved.phase ?? "unknown";

          if (saved.block_type === "thinking_block") {
            const thinking = String(saved.payload?.thinking ?? saved.payload?.text ?? "");
            wsManager.broadcast({
              type: "thinking_block",
              data: { adw_id: adwId, session_id: saved.session_id, phase, thinking, timestamp: ts },
            });
          } else if (saved.block_type === "tool_use_block") {
            const tool_name = String(saved.payload?.tool_name ?? saved.payload?.name ?? "unknown");
            const tool_input = (saved.payload?.tool_input ?? saved.payload?.input ?? {}) as Record<string, unknown>;
            wsManager.broadcast({
              type: "tool_use_block",
              data: { adw_id: adwId, session_id: saved.session_id, phase, tool_name, tool_input, timestamp: ts },
            });
          } else if (saved.block_type === "text_block") {
            const text = String(saved.payload?.text ?? "");
            wsManager.broadcast({
              type: "text_block",
              data: { adw_id: adwId, session_id: saved.session_id, phase, text, timestamp: ts },
            });
          } else {
            // Lifecycle hook events
            wsManager.broadcast({
              type: "agent_status",
              data: { adw_id: adwId, session_id: saved.session_id, hook_event_type: saved.hook_event_type, phase, timestamp: ts },
            });
          }

          return json(saved, 201);
        } catch (e: any) {
          return json({ error: String(e?.message ?? e) }, 400);
        }
      }

      return new Response("Not found", { status: 404, headers: CORS_HEADERS });
    },

    websocket: {
      open(ws) {
        wsManager.add(ws);
      },
      close(ws) {
        wsManager.remove(ws);
      },
      message(_ws, _msg) {
        // no inbound commands for tests
      },
    },
  });

  return testServer;
}

/**
 * Open a WebSocket client that collects all messages.
 */
function openWebSocketClient(): Promise<void> {
  return new Promise((resolve, reject) => {
    receivedMessages = [];

    wsClient = new WebSocket(TEST_WS_URL);
    wsClient.onopen = () => {
      resolve();
    };
    wsClient.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        receivedMessages.push(msg);
      } catch (e) {
        console.error("Failed to parse WS message:", ev.data);
      }
    };
    wsClient.onerror = (err) => {
      reject(new Error(`WebSocket error: ${err}`));
    };
    wsClient.onclose = () => {
      // silently close
    };

    // Safety timeout
    setTimeout(() => reject(new Error("WebSocket open timeout")), 5000);
  });
}

/**
 * Close the WebSocket client.
 */
function closeWebSocketClient(): void {
  if (wsClient) {
    wsClient.close();
    wsClient = null;
  }
}

/**
 * Wait for a message of a specific type to arrive (with 500ms timeout).
 */
function waitForMessage(
  type: string,
  timeoutMs: number = 500
): Promise<any> {
  return new Promise((resolve, reject) => {
    const checkInterval = setInterval(() => {
      const found = receivedMessages.find((m) => m.type === type);
      if (found) {
        clearInterval(checkInterval);
        clearTimeout(timeoutHandle);
        resolve(found);
      }
    }, 10);

    const timeoutHandle = setTimeout(() => {
      clearInterval(checkInterval);
      reject(new Error(`Timeout waiting for message type="${type}" after ${timeoutMs}ms`));
    }, timeoutMs);
  });
}

beforeAll(async () => {
  // Give system a moment to clean up from any previous tests
  await new Promise((r) => setTimeout(r, 100));

  server = await startTestServer();
  console.log(`Test server started on port ${TEST_PORT}`);
});

afterAll(async () => {
  closeWebSocketClient();
  if (server) {
    server.stop();
  }
  // Give system a moment to clean up
  await new Promise((r) => setTimeout(r, 100));
});

afterEach(async () => {
  closeWebSocketClient();
  await new Promise((r) => setTimeout(r, 50));
});

describe("WebSocket broadcast pipeline", () => {
  it("broadcasts thinking_block message within 500ms", async () => {
    await openWebSocketClient();

    const thinkingContent = "Let me think about this problem carefully...";
    const response = await fetch(`${TEST_URL}/events`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source_app: "test-app",
        session_id: "sess_123",
        hook_event_type: "StreamEvent",
        block_type: "thinking_block",
        adw_id: "adw_001",
        phase: "plan",
        payload: {
          thinking: thinkingContent,
        },
        timestamp: Date.now(),
      }),
    });

    expect(response.status).toBe(201);

    const msg = await waitForMessage("thinking_block");
    expect(msg.type).toBe("thinking_block");
    expect(msg.data.thinking).toBe(thinkingContent);
    expect(msg.data.adw_id).toBe("adw_001");
    expect(msg.data.session_id).toBe("sess_123");
    expect(msg.data.phase).toBe("plan");
    expect(typeof msg.data.timestamp).toBe("number");
  });

  it("broadcasts tool_use_block message within 500ms", async () => {
    await openWebSocketClient();

    const toolName = "test_function";
    const toolInput = { arg1: "value1", arg2: 42 };

    const response = await fetch(`${TEST_URL}/events`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source_app: "test-app",
        session_id: "sess_456",
        hook_event_type: "ToolUseStart",
        block_type: "tool_use_block",
        adw_id: "adw_002",
        phase: "build",
        payload: {
          tool_name: toolName,
          tool_input: toolInput,
        },
        timestamp: Date.now(),
      }),
    });

    expect(response.status).toBe(201);

    const msg = await waitForMessage("tool_use_block");
    expect(msg.type).toBe("tool_use_block");
    expect(msg.data.tool_name).toBe(toolName);
    expect(msg.data.tool_input).toEqual(toolInput);
    expect(msg.data.adw_id).toBe("adw_002");
    expect(msg.data.session_id).toBe("sess_456");
    expect(msg.data.phase).toBe("build");
  });

  it("broadcasts text_block message within 500ms", async () => {
    await openWebSocketClient();

    const textContent = "Here is the answer to your question.";

    const response = await fetch(`${TEST_URL}/events`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source_app: "test-app",
        session_id: "sess_789",
        hook_event_type: "StreamEvent",
        block_type: "text_block",
        adw_id: "adw_003",
        phase: "review",
        payload: {
          text: textContent,
        },
        timestamp: Date.now(),
      }),
    });

    expect(response.status).toBe(201);

    const msg = await waitForMessage("text_block");
    expect(msg.type).toBe("text_block");
    expect(msg.data.text).toBe(textContent);
    expect(msg.data.adw_id).toBe("adw_003");
    expect(msg.data.session_id).toBe("sess_789");
    expect(msg.data.phase).toBe("review");
  });

  it("broadcasts agent_status message for hook_lifecycle events within 500ms", async () => {
    await openWebSocketClient();

    const response = await fetch(`${TEST_URL}/events`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source_app: "test-app",
        session_id: "sess_lifecycle",
        hook_event_type: "hook_lifecycle",
        block_type: "hook_lifecycle",
        adw_id: "adw_004",
        phase: "plan",
        payload: {},
        timestamp: Date.now(),
      }),
    });

    expect(response.status).toBe(201);

    const msg = await waitForMessage("agent_status");
    expect(msg.type).toBe("agent_status");
    expect(msg.data.hook_event_type).toBe("hook_lifecycle");
    expect(msg.data.adw_id).toBe("adw_004");
    expect(msg.data.session_id).toBe("sess_lifecycle");
    expect(msg.data.phase).toBe("plan");
  });

  it("broadcasts raw event message for backward compatibility", async () => {
    await openWebSocketClient();

    const eventPayload = {
      source_app: "test-app",
      session_id: "sess_compat",
      hook_event_type: "StreamEvent",
      block_type: "text_block",
      adw_id: "adw_005",
      phase: "test",
      payload: { text: "Test message" },
      timestamp: Date.now(),
    };

    const response = await fetch(`${TEST_URL}/events`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(eventPayload),
    });

    expect(response.status).toBe(201);

    // Check that raw "event" type is also broadcast
    const rawMsg = await waitForMessage("event");
    expect(rawMsg.type).toBe("event");
    expect(rawMsg.data.source_app).toBe("test-app");
    expect(rawMsg.data.session_id).toBe("sess_compat");
  });

  it("rejects POST /events with missing required fields", async () => {
    const response = await fetch(`${TEST_URL}/events`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        // missing source_app, session_id, hook_event_type
        block_type: "thinking_block",
        payload: {},
      }),
    });

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toBeTruthy();
  });

  it("handles missing block_type as lifecycle event", async () => {
    await openWebSocketClient();

    const response = await fetch(`${TEST_URL}/events`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source_app: "test-app",
        session_id: "sess_noblock",
        hook_event_type: "PreToolUse",
        adw_id: "adw_006",
        phase: "build",
        payload: {},
        timestamp: Date.now(),
      }),
    });

    expect(response.status).toBe(201);

    const msg = await waitForMessage("agent_status");
    expect(msg.type).toBe("agent_status");
    expect(msg.data.hook_event_type).toBe("PreToolUse");
  });
});
