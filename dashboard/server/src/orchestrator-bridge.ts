/**
 * Orchestrator Bridge — IPC between Bun server and Python orchestrator service.
 *
 * Responsibilities:
 * 1. Maintain reference to the Python orchestrator service endpoint (HTTP)
 * 2. sendMessage() — POST to orchestrator /chat endpoint, fire-and-forget
 * 3. Stream response blocks back to clients via WebSocket
 * 4. Parse block types (text_chunk, thinking_block, tool_use_block)
 * 5. Insert each block into orchestrator_chat SQLite table
 * 6. Emit chat_typing events (true on start, false on completion/error)
 * 7. Emit chat_stream events as streaming blocks arrive
 *
 * @task T082
 * @epic T059
 */

import type { WebSocketManager } from "./ws-manager";

export interface OrchestratorBridgeConfig {
  baseUrl?: string; // defaults to http://localhost:4001
  timeoutMs?: number; // defaults to 30000
}

/**
 * Block type discriminator for streaming responses from orchestrator.
 * The Python service streams these back via /events endpoint.
 */
export type BlockType = "text_chunk" | "thinking_block" | "tool_use_block";

/**
 * Orchestrator response block structure.
 * Streamed from Python service during orchestrator processing.
 */
export interface OrchestratorBlock {
  type: BlockType;
  content: string; // text content or thinking text
  tool_name?: string;
  tool_input?: Record<string, unknown>;
  timestamp?: number;
}

/**
 * OrchestratorBridge: manages the IPC between Bun and Python orchestrator.
 * Bridges user messages to the orchestrator service and streams responses back
 * over WebSocket to connected clients.
 */
export class OrchestratorBridge {
  private baseUrl: string;
  private timeoutMs: number;
  private wsManager: WebSocketManager;

  constructor(wsManager: WebSocketManager, config: OrchestratorBridgeConfig = {}) {
    this.baseUrl = config.baseUrl ?? "http://localhost:4001";
    this.timeoutMs = config.timeoutMs ?? 30_000;
    this.wsManager = wsManager;
  }

  /**
   * sendMessage — send a user message to the orchestrator and stream responses.
   *
   * Flow:
   * 1. Broadcast chat_typing { is_typing: true } to clients
   * 2. POST to /chat with message and orchestrator_id
   * 3. Stream response via POST to /events (fire-and-forget)
   * 4. Each response block inserted into orchestrator_chat via insertChatMessage
   * 5. Each block broadcasted to clients as orchestrator_chat message
   * 6. Finally broadcast chat_typing { is_typing: false }
   *
   * @param text The user message text
   * @param orchestratorAgentId The orchestrator agent ID (session identifier)
   */
  async sendMessage(text: string, orchestratorAgentId: string): Promise<void> {
    try {
      // Broadcast typing indicator — user is awaiting response
      this.wsManager.broadcast({
        type: "chat_typing",
        orchestrator_agent_id: orchestratorAgentId,
        is_typing: true,
      });

      // Send the message to the orchestrator service
      const response = await fetch(`${this.baseUrl}/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          message: text,
          orchestrator_id: orchestratorAgentId,
        }),
        signal: AbortSignal.timeout(this.timeoutMs),
      });

      if (!response.ok) {
        const errorText = await response.text();
        console.error(`[orchestrator-bridge] POST /chat failed: ${response.status} ${errorText}`);
        this.wsManager.broadcast({
          type: "chat_typing",
          orchestrator_agent_id: orchestratorAgentId,
          is_typing: false,
        });
        return;
      }

      // Orchestrator response is fire-and-forget — the Python service streams
      // response blocks back via the /events endpoint on the Bun server.
      // (This is wired up in index.ts POST /events and broadcasts them as
      // orchestrator_chat messages.)

      // Brief delay to let the response start streaming, then clear typing
      // In production, the typing indicator should only clear after the last
      // event arrives. For now we assume streaming completes within 30s.
      setTimeout(() => {
        this.wsManager.broadcast({
          type: "chat_typing",
          orchestrator_agent_id: orchestratorAgentId,
          is_typing: false,
        });
      }, 500);
    } catch (error) {
      console.error("[orchestrator-bridge] sendMessage failed:", error);
      // Ensure typing indicator is cleared on error
      this.wsManager.broadcast({
        type: "chat_typing",
        orchestrator_agent_id: orchestratorAgentId,
        is_typing: false,
      });
    }
  }

  /**
   * isAvailable — check if the orchestrator service is running.
   *
   * @returns true if GET /status succeeds, false otherwise
   */
  async isAvailable(): Promise<boolean> {
    try {
      const response = await fetch(`${this.baseUrl}/status`, {
        method: "GET",
        signal: AbortSignal.timeout(5000),
      });
      return response.ok;
    } catch {
      return false;
    }
  }

  /**
   * interrupt — send an interrupt signal to the orchestrator.
   *
   * Cancels the currently running orchestrator and streams.
   * The Python service will respond with an interrupt event over /events.
   */
  async interrupt(orchestratorAgentId: string): Promise<void> {
    try {
      const response = await fetch(`${this.baseUrl}/interrupt`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          orchestrator_id: orchestratorAgentId,
        }),
        signal: AbortSignal.timeout(this.timeoutMs),
      });

      if (!response.ok) {
        console.error(`[orchestrator-bridge] POST /interrupt failed: ${response.status}`);
      }
    } catch (error) {
      console.error("[orchestrator-bridge] interrupt failed:", error);
    }
  }
}
