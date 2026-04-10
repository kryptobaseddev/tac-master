<template>
  <div class="orchestrator-chat-panel">
    <!-- Check if backend is available -->
    <div v-if="backendError" class="error-fallback">
      <div class="error-icon">⚠️</div>
      <p class="error-title">Chat Unavailable</p>
      <p class="error-subtitle">
        {{ backendError }}
      </p>
      <button @click="retry" class="retry-button">Retry Connection</button>
    </div>

    <!-- Render OrchestratorChat when available -->
    <OrchestratorChat v-else />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from "vue";
import OrchestratorChat from "./OrchestratorChat.vue";

// State
const backendError = ref<string | null>(null);

// Check if backend chat endpoints are available
const checkBackendAvailability = async () => {
  try {
    const response = await fetch("/api/chat/health", {
      method: "GET",
      headers: {
        "Accept": "application/json",
      },
    });

    if (!response.ok) {
      // If 404, endpoint doesn't exist yet — graceful fallback
      if (response.status === 404) {
        backendError.value = "Chat backend not yet configured. Please check the server.";
      } else {
        backendError.value = `Backend error: ${response.status}`;
      }
    } else {
      backendError.value = null;
    }
  } catch (err) {
    // Network error or endpoint doesn't exist
    backendError.value = "Unable to reach chat backend. Please check your connection.";
    console.warn("[OrchestratorChatPanel] backend check failed:", err);
  }
};

// Retry connection
const retry = () => {
  backendError.value = null;
  checkBackendAvailability();
};

// Check backend on mount
onMounted(async () => {
  await checkBackendAvailability();
});
</script>

<style scoped>
.orchestrator-chat-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  width: 100%;
}

/* Error fallback state */
.error-fallback {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  padding: var(--spacing-xl);
  background: var(--bg-secondary);
  border-left: 1px solid var(--border-color);
  text-align: center;
}

.error-icon {
  font-size: 3rem;
  margin-bottom: var(--spacing-md);
  opacity: 0.5;
}

.error-title {
  font-size: 1rem;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0 0 var(--spacing-xs);
}

.error-subtitle {
  font-size: 0.875rem;
  color: var(--text-muted);
  max-width: 320px;
  margin: 0 0 var(--spacing-md);
}

.retry-button {
  padding: 0.5rem 1rem;
  background: var(--accent-primary);
  color: #000;
  border: none;
  border-radius: 6px;
  font-size: 0.875rem;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s ease;
}

.retry-button:hover {
  background: rgba(6, 182, 212, 0.85);
  transform: scale(1.05);
}

.retry-button:active {
  transform: scale(0.97);
}
</style>
