<template>
  <div class="orchestrator-chat">
    <!-- Header -->
    <div class="chat-header">
      <h3>Orchestrator Chat</h3>
      <div class="header-status">
        <span
          class="status-dot"
          :class="isTyping ? 'status-dot--active' : 'status-dot--idle'"
        ></span>
        <span class="status-label">{{ isTyping ? "Processing..." : "Ready" }}</span>
      </div>
    </div>

    <!-- Message list -->
    <div class="chat-messages" ref="messagesRef">
      <!-- Empty state -->
      <div v-if="messages.length === 0" class="empty-state">
        <div class="empty-icon">💬</div>
        <p class="empty-title">Start a conversation</p>
        <p class="empty-subtitle">
          Send a message to the orchestrator agent
        </p>
      </div>

      <!-- Messages -->
      <div v-else class="message-list">
        <div
          v-for="message in messages"
          :key="message.id"
          class="message-wrapper"
          :class="`message-${message.sender}`"
        >
          <!-- Text message -->
          <div v-if="message.type === 'text'" class="message-bubble">
            <div class="message-header">
              <span class="message-sender-label">{{
                message.sender === "user" ? "YOU" : "ORCHESTRATOR"
              }}</span>
              <span class="message-time">{{
                formatTime(message.timestamp)
              }}</span>
            </div>
            <div
              class="message-content"
              v-html="formatContent((message as TextChatMessage).content)"
            ></div>
          </div>

          <!-- Thinking block -->
          <ThinkingBubble
            v-else-if="message.type === 'thinking'"
            :thinking="(message as ThinkingChatMessage).thinking"
            :timestamp="message.timestamp"
          />

          <!-- Tool use block -->
          <ToolUseBubble
            v-else-if="message.type === 'tool_use'"
            :tool-name="(message as ToolUseChatMessage).toolName"
            :tool-input="(message as ToolUseChatMessage).toolInput"
            :timestamp="message.timestamp"
          />
        </div>
      </div>

      <!-- Typing indicator -->
      <div
        v-if="isTyping"
        class="message-wrapper message-orchestrator typing-indicator-wrapper"
      >
        <div class="message-bubble typing-indicator">
          <div class="message-sender-label">ORCHESTRATOR</div>
          <div class="typing-dots">
            <span></span>
            <span></span>
            <span></span>
          </div>
        </div>
      </div>

      <!-- Auto-scroll anchor -->
      <div ref="bottomRef"></div>
    </div>

    <!-- Input bar -->
    <div class="chat-input-bar">
      <textarea
        ref="inputRef"
        v-model="inputText"
        class="chat-input"
        placeholder="Message the orchestrator... (Enter to send, Shift+Enter for newline)"
        :disabled="isTyping"
        rows="1"
        @keydown="handleKeydown"
        @input="autoResizeTextarea"
      ></textarea>
      <button
        class="send-button"
        :disabled="isTyping || !inputText.trim()"
        @click="sendMessage"
        title="Send message"
      >
        <svg
          v-if="!isTyping"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
        >
          <line x1="22" y1="2" x2="11" y2="13"></line>
          <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
        </svg>
        <svg
          v-else
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
        >
          <circle cx="12" cy="12" r="10"></circle>
          <path d="M12 8v4M12 16h.01"></path>
        </svg>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted } from "vue";
import ThinkingBubble from "./chat/ThinkingBubble.vue";
import ToolUseBubble from "./chat/ToolUseBubble.vue";
import type {
  ChatMessage,
  TextChatMessage,
  ThinkingChatMessage,
  ToolUseChatMessage,
} from "../types";
import { renderMarkdown } from "../utils/markdown";
import { useChatStore } from "../stores/chatStore";
import { useOrchestratorStore } from "../stores/orchestratorStore";
import * as chatService from "../services/chatService";

// Stores
const chatStore = useChatStore();
const orchestratorStore = useOrchestratorStore();

// DOM refs
const messagesRef = ref<HTMLElement>();
const bottomRef = ref<HTMLElement>();
const inputRef = ref<HTMLTextAreaElement>();

// Local state
const inputText = ref<string>("");

// Reactive bindings from chatStore
const messages = computed<ChatMessage[]>(() => chatStore.messages);
const isTyping = computed<boolean>(() => chatStore.isTyping);

// Scroll to bottom helper
const scrollToBottom = async () => {
  await nextTick();
  bottomRef.value?.scrollIntoView({ behavior: "smooth" });
};

// Auto-scroll when messages change
watch(
  () => messages.value.length,
  () => {
    if (chatStore.autoScroll) {
      scrollToBottom();
    }
  }
);

// Auto-scroll when typing indicator changes
watch(isTyping, () => {
  if (chatStore.autoScroll) {
    scrollToBottom();
  }
});

// Auto-scroll when last message content changes (streaming)
watch(
  () => {
    if (messages.value.length > 0) {
      const last = messages.value[messages.value.length - 1];
      return last?.type === "text"
        ? (last as TextChatMessage).content
        : last?.id;
    }
    return "";
  },
  () => {
    if (chatStore.autoScroll) {
      scrollToBottom();
    }
  }
);

// Format helpers
const formatTime = (timestamp: string | Date): string => {
  const date = typeof timestamp === "string" ? new Date(timestamp) : timestamp;
  return date.toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
  });
};

const formatContent = (content: string): string => {
  return renderMarkdown(content);
};

// Auto-resize textarea on input
const autoResizeTextarea = () => {
  const el = inputRef.value;
  if (!el) return;
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 120) + "px";
};

// Enter to send, Shift+Enter for newline
const handleKeydown = (e: KeyboardEvent) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
};

// Send message
const sendMessage = async () => {
  const text = inputText.value.trim();
  if (!text || isTyping.value) return;

  const agentId =
    chatStore.orchestratorAgentId ||
    orchestratorStore.orchestratorAgentId ||
    "tac-master";

  // Optimistically add user message to the chat
  const userMsg: TextChatMessage = {
    id: `user-${Date.now()}`,
    sender: "user",
    type: "text",
    content: text,
    timestamp: new Date().toISOString(),
  };
  chatStore.addMessage(userMsg);

  // Clear input and reset textarea height
  inputText.value = "";
  if (inputRef.value) {
    inputRef.value.style.height = "auto";
  }

  // Set typing so orchestrator side shows it's working
  chatStore.setTyping(true);

  try {
    await chatService.sendMessage(text, agentId);
  } catch (err) {
    console.error("[OrchestratorChat] sendMessage failed:", err);
    // Add an error notice to chat
    const errMsg: TextChatMessage = {
      id: `err-${Date.now()}`,
      sender: "orchestrator",
      type: "text",
      content: "Failed to send message. Please check the server connection.",
      timestamp: new Date().toISOString(),
    };
    chatStore.addMessage(errMsg);
    chatStore.setTyping(false);
  }
};

// Load history on mount
onMounted(async () => {
  const agentId =
    chatStore.orchestratorAgentId ||
    orchestratorStore.orchestratorAgentId ||
    "tac-master";

  if (agentId) {
    try {
      await chatStore.loadHistory(agentId);
    } catch (err) {
      // History not available yet — not an error, just no prior session
      console.warn("[OrchestratorChat] No history available:", err);
    }
  }

  // Scroll to bottom after loading history
  await nextTick();
  setTimeout(() => scrollToBottom(), 100);
});
</script>

<style scoped>
.orchestrator-chat {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--bg-secondary);
  border-left: 1px solid var(--border-color);
}

/* ─── Header ─── */
.chat-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: var(--spacing-md);
  border-bottom: 1px solid var(--border-color);
  background: rgba(0, 0, 0, 0.2);
  flex-shrink: 0;
}

.chat-header h3 {
  font-size: 0.875rem;
  font-weight: 700;
  letter-spacing: 0.05em;
  color: var(--accent-primary);
  margin: 0;
}

.header-status {
  display: flex;
  align-items: center;
  gap: 6px;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  transition: background 0.3s ease;
}

.status-dot--idle {
  background: var(--text-muted);
}

.status-dot--active {
  background: var(--accent-primary);
  animation: pulse-dot 1.2s infinite;
}

@keyframes pulse-dot {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}

.status-label {
  font-size: 0.75rem;
  color: var(--text-muted);
}

/* ─── Message area ─── */
.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: var(--spacing-md);
  display: flex;
  flex-direction: column;
  min-height: 0;
}

/* Empty state */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  text-align: center;
  padding: var(--spacing-xl);
}

.empty-icon {
  font-size: 3rem;
  margin-bottom: var(--spacing-md);
  opacity: 0.3;
}

.empty-title {
  font-size: 1rem;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0 0 var(--spacing-xs);
}

.empty-subtitle {
  font-size: 0.875rem;
  color: var(--text-muted);
  max-width: 280px;
  margin: 0;
}

/* Message list */
.message-list {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
}

.message-wrapper {
  display: flex;
  animation: fadeIn 0.25s ease-out;
}

.message-user {
  justify-content: flex-end;
}

.message-orchestrator {
  justify-content: flex-start;
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(6px); }
  to   { opacity: 1; transform: translateY(0); }
}

/* Message bubble */
.message-bubble {
  max-width: 85%;
  padding: var(--spacing-md);
  border-radius: 12px;
  word-wrap: break-word;
}

.message-user .message-bubble {
  background: rgba(6, 182, 212, 0.15);
  border: 1px solid rgba(6, 182, 212, 0.3);
  border-bottom-right-radius: 4px;
}

.message-orchestrator .message-bubble {
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid var(--border-color);
  border-bottom-left-radius: 4px;
}

.message-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--spacing-xs);
  gap: var(--spacing-md);
}

.message-sender-label {
  font-size: 0.7rem;
  font-weight: 700;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: var(--text-muted);
}

.message-user .message-sender-label {
  color: var(--accent-primary);
}

.message-time {
  font-size: 0.7rem;
  color: var(--text-dim, rgba(255,255,255,0.3));
}

.message-content {
  font-size: 0.875rem;
  line-height: 1.5;
  color: var(--text-primary);
}

/* Markdown deep styling */
.message-content :deep(p) { margin: 0; }
.message-content :deep(p:not(:last-child)) { margin-bottom: 0.5em; }
.message-content :deep(strong) { font-weight: 700; }
.message-content :deep(em) { font-style: italic; }
.message-content :deep(code) {
  background: rgba(0,0,0,0.3);
  padding: 2px 4px;
  border-radius: 3px;
  font-family: "JetBrains Mono", monospace;
  font-size: 0.9em;
}
.message-content :deep(a) { color: var(--accent-primary); text-decoration: none; }
.message-content :deep(a:hover) { text-decoration: underline; }
.message-content :deep(pre) {
  background: rgba(0,0,0,0.3);
  padding: 8px;
  border-radius: 4px;
  overflow-x: auto;
  margin: 0.5em 0;
  font-family: "JetBrains Mono", monospace;
}
.message-content :deep(ul),
.message-content :deep(ol) { margin: 0.25em 0; padding-left: 20px; }
.message-content :deep(li) { margin: 0.25em 0; }

/* Typing indicator */
.typing-indicator-wrapper {
  margin-top: var(--spacing-md);
}

.typing-indicator {
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid var(--border-color);
}

.typing-dots {
  display: flex;
  gap: 4px;
  padding: var(--spacing-xs) 0;
}

.typing-dots span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--text-muted);
  animation: typing 1.4s infinite;
}

.typing-dots span:nth-child(2) { animation-delay: 0.2s; }
.typing-dots span:nth-child(3) { animation-delay: 0.4s; }

@keyframes typing {
  0%, 60%, 100% { opacity: 0.3; transform: translateY(0); }
  30%            { opacity: 1;   transform: translateY(-4px); }
}

/* ─── Input bar ─── */
.chat-input-bar {
  display: flex;
  align-items: flex-end;
  gap: var(--spacing-sm);
  padding: var(--spacing-sm) var(--spacing-md);
  border-top: 1px solid var(--border-color);
  background: rgba(0, 0, 0, 0.2);
  flex-shrink: 0;
}

.chat-input {
  flex: 1;
  min-height: 38px;
  max-height: 120px;
  padding: 0.5rem 0.75rem;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  color: var(--text-primary);
  font-size: 0.875rem;
  font-family: inherit;
  line-height: 1.5;
  resize: none;
  outline: none;
  overflow-y: auto;
  transition: border-color 0.2s ease;
}

.chat-input:focus {
  border-color: var(--accent-primary);
}

.chat-input:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.chat-input::placeholder {
  color: var(--text-muted);
  opacity: 0.6;
}

.send-button {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  flex-shrink: 0;
  padding: 0;
  background: var(--accent-primary);
  border: none;
  border-radius: 8px;
  color: #000;
  cursor: pointer;
  transition: all 0.2s ease;
}

.send-button:hover:not(:disabled) {
  background: rgba(6, 182, 212, 0.85);
  transform: scale(1.05);
}

.send-button:active:not(:disabled) {
  transform: scale(0.97);
}

.send-button:disabled {
  background: rgba(6, 182, 212, 0.3);
  cursor: not-allowed;
  color: rgba(0, 0, 0, 0.5);
}

.send-button svg {
  width: 16px;
  height: 16px;
}
</style>
