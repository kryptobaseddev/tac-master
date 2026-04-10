/**
 * Chat store for tac-master dashboard (T089).
 *
 * Pinia store that manages the state of the human-to-orchestrator conversation:
 * - messages: array of ChatMessage (text, thinking, tool_use)
 * - isTyping: boolean flag for orchestrator activity
 * - autoScroll: whether to auto-scroll to bottom on new messages
 * - orchestratorAgentId: the active orchestrator session UUID
 *
 * Actions:
 * - addMessage(msg): append a message
 * - setTyping(bool): set orchestrator activity state
 * - loadHistory(agentId, limit): populate messages from server
 * - clearMessages(): reset conversation
 *
 * WebSocket routing (in App.vue or orchestratorStore) dispatches incoming
 * events (orchestrator_chat, thinking_block, tool_use_block, chat_typing, chat_stream)
 * to the appropriate actions.
 *
 * Follows the T059 chat interface specification.
 */

import { defineStore } from "pinia";
import { ref, computed } from "vue";
import * as chatService from "../services/chatService";
import type { ChatMessage } from "../types";

export const useChatStore = defineStore("chat", () => {
  // --- state ---
  const messages = ref<ChatMessage[]>([]);
  const isTyping = ref<boolean>(false);
  const autoScroll = ref<boolean>(true);
  const orchestratorAgentId = ref<string | null>(null);

  // --- computed ---
  const messageCount = computed(() => messages.value.length);

  // --- actions ---

  /**
   * Add a message to the chat.
   *
   * @param msg ChatMessage to append
   */
  function addMessage(msg: ChatMessage) {
    messages.value.push(msg);
  }

  /**
   * Set the typing indicator state.
   *
   * @param typing true if orchestrator is processing, false otherwise
   */
  function setTyping(typing: boolean) {
    isTyping.value = typing;
  }

  /**
   * Load chat history from the server.
   *
   * Maps the server response format to the ChatMessage interface:
   * - sender_type: 'user' | 'orchestrator' → becomes sender: 'user' | 'orchestrator'
   * - metadata.type: 'text_chunk' | 'thinking' | 'tool_use' → becomes type
   * - For text_chunk: content = message
   * - For thinking: thinking = metadata.thinking (extract from payload)
   * - For tool_use: toolName, toolInput from metadata
   *
   * @param agentId orchestrator_agent_id
   * @param limit maximum number of messages (default: 50)
   */
  async function loadHistory(agentId: string, limit: number = 50) {
    try {
      orchestratorAgentId.value = agentId;
      const response = await chatService.loadHistory(agentId, limit);

      // Map server response to ChatMessage[] format
      const mapped: ChatMessage[] = response.messages.map((msg) => {
        const metadata = msg.metadata || {};
        const type = metadata.type as string;

        // Determine the normalized message type
        let normalizedType: "text" | "thinking" | "tool_use";
        if (type === "text_chunk" || type === "text") {
          normalizedType = "text";
        } else if (type === "thinking") {
          normalizedType = "thinking";
        } else if (type === "tool_use") {
          normalizedType = "tool_use";
        } else {
          normalizedType = "text"; // fallback
        }

        const baseMsg = {
          id: msg.id,
          sender: msg.sender_type as "user" | "orchestrator",
          timestamp: msg.created_at || new Date().toISOString(),
          type: normalizedType,
        };

        if (normalizedType === "text") {
          return {
            ...baseMsg,
            type: "text" as const,
            content: msg.message,
          };
        } else if (normalizedType === "thinking") {
          return {
            ...baseMsg,
            type: "thinking" as const,
            thinking: metadata.thinking || msg.message,
          };
        } else {
          // tool_use
          return {
            ...baseMsg,
            type: "tool_use" as const,
            toolName: metadata.tool_name || "unknown",
            toolInput: metadata.tool_input || null,
          };
        }
      });

      messages.value = mapped;
    } catch (err) {
      console.error("Failed to load chat history:", err);
      throw err;
    }
  }

  /**
   * Clear all messages from the conversation.
   */
  function clearMessages() {
    messages.value = [];
    isTyping.value = false;
  }

  /**
   * Set the orchestrator agent ID.
   *
   * @param agentId UUID of the orchestrator session
   */
  function setOrchestratorAgentId(agentId: string) {
    orchestratorAgentId.value = agentId;
  }

  /**
   * Toggle auto-scroll behavior.
   *
   * @param enabled whether auto-scroll should be active
   */
  function setAutoScroll(enabled: boolean) {
    autoScroll.value = enabled;
  }

  return {
    // state
    messages,
    isTyping,
    autoScroll,
    orchestratorAgentId,
    // computed
    messageCount,
    // actions
    addMessage,
    setTyping,
    loadHistory,
    clearMessages,
    setOrchestratorAgentId,
    setAutoScroll,
  };
});
