/**
 * WebSocket connection manager for tac-master dashboard.
 *
 * Encapsulates client set management and typed broadcast methods for streaming events.
 * Handles client disconnection gracefully by removing from active set on send errors.
 */

import type {
  WsMessage,
  ThinkingBlockData,
  ToolUseBlockData,
  TextBlockData,
  AgentStatusData,
  HeartbeatData,
} from "./types";

export class WebSocketManager {
  private clients: Set<any>;

  constructor() {
    this.clients = new Set();
  }

  /**
   * Add a new client connection.
   */
  add(ws: any): void {
    this.clients.add(ws);
  }

  /**
   * Remove a client connection.
   */
  remove(ws: any): void {
    this.clients.delete(ws);
  }

  /**
   * Get the count of active clients.
   */
  size(): number {
    return this.clients.size;
  }

  /**
   * Broadcast a typed WsMessage to all connected clients.
   * Removes clients from the set if send fails.
   */
  broadcast(msg: WsMessage): void {
    const payload = JSON.stringify(msg);
    for (const ws of this.clients) {
      try {
        ws.send(payload);
      } catch {
        this.clients.delete(ws);
      }
    }
  }

  /**
   * Broadcast a thinking block to all clients.
   */
  broadcastThinkingBlock(data: ThinkingBlockData): void {
    this.broadcast({ type: "thinking_block", data });
  }

  /**
   * Broadcast a tool use block to all clients.
   */
  broadcastToolUseBlock(data: ToolUseBlockData): void {
    this.broadcast({ type: "tool_use_block", data });
  }

  /**
   * Broadcast a text block to all clients.
   */
  broadcastTextBlock(data: TextBlockData): void {
    this.broadcast({ type: "text_block", data });
  }

  /**
   * Broadcast an agent status update to all clients.
   */
  broadcastAgentStatus(data: AgentStatusData): void {
    this.broadcast({ type: "agent_status", data });
  }

  /**
   * Broadcast a heartbeat to all clients.
   */
  broadcastHeartbeat(activeClients?: number): void {
    const data: HeartbeatData = {
      timestamp: Date.now(),
      active_clients: activeClients ?? this.clients.size,
    };
    this.broadcast({ type: "heartbeat", data });
  }
}
