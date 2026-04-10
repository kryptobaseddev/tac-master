/**
 * Chat service for tac-master dashboard (T089).
 *
 * Provides HTTP client methods to interact with the backend chat API:
 * - sendMessage(text, agentId): POST /api/chat/send
 * - loadHistory(agentId, limit): POST /api/chat/history
 *
 * Follows the T059 chat interface specification.
 */

import type { SendChatRequest, SendChatResponse, LoadChatResponse } from "../types";

/**
 * Send a user message to the orchestrator.
 *
 * @param text The user's message text
 * @param agentId The orchestrator_agent_id (UUID of the orchestrator session)
 * @returns Promise resolving to the server response
 * @throws Error if the request fails
 */
export async function sendMessage(
  text: string,
  agentId: string
): Promise<SendChatResponse> {
  const request: SendChatRequest = {
    message: text,
    orchestrator_agent_id: agentId,
  };

  const response = await fetch("/api/chat/send", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    throw new Error(
      `Failed to send message: ${response.status} ${response.statusText}`
    );
  }

  return response.json();
}

/**
 * Load chat history for an orchestrator session.
 *
 * @param agentId The orchestrator_agent_id (UUID of the orchestrator session)
 * @param limit Maximum number of messages to retrieve (default: 50)
 * @returns Promise resolving to chat history and turn count
 * @throws Error if the request fails
 */
export async function loadHistory(
  agentId: string,
  limit: number = 50
): Promise<LoadChatResponse> {
  const request = {
    orchestrator_agent_id: agentId,
    limit,
  };

  const response = await fetch("/api/chat/history", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    throw new Error(
      `Failed to load chat history: ${response.status} ${response.statusText}`
    );
  }

  return response.json();
}
