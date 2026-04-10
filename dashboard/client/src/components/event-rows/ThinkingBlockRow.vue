<!--
  ThinkingBlockRow — renders a thinking block event in the EventStream.

  Displays a collapsible cyan bubble for streaming thinking content.
  Mapped from ThinkingBlockWsMessage data stored in orchestratorStore.
-->
<template>
  <div class="thinking-block-row" :class="{ 'is-expanded': isExpanded }">
    <div class="event-line-number">{{ lineNumber }}</div>

    <div class="thinking-badge">
      THINKING
    </div>

    <div class="thinking-header">
      <span class="agent-label">{{ agentLabel }}</span>
      <span v-if="phase" class="phase-label">{{ phase }}</span>
    </div>

    <div class="event-content">
      <div class="thinking-bubble" @click="toggleExpanded">
        <div class="bubble-header">
          <span class="bubble-title">Reasoning</span>
          <button class="expand-btn" @click.stop="toggleExpanded" :title="isExpanded ? 'Collapse' : 'Expand'">
            {{ isExpanded ? '[ hide ]' : '[ show ]' }}
          </button>
        </div>
        <div v-if="isExpanded" class="bubble-content">
          <div class="thinking-text" v-html="renderedThinking"></div>
        </div>
        <div v-else class="bubble-preview">
          <span class="preview-text">{{ previewText }}</span>
        </div>
      </div>
    </div>

    <div class="event-meta">
      <span class="event-time">{{ formatTime(timestamp) }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { renderMarkdown } from '../../utils/markdown'

interface Props {
  event: {
    id: string
    orchestrator_agent_id: string
    thinking: string
    timestamp: number
    phase?: string
  }
  lineNumber: number
}

const props = defineProps<Props>()
const isExpanded = ref(false)

const toggleExpanded = () => {
  isExpanded.value = !isExpanded.value
}

const agentLabel = computed(() => {
  const id = props.event.orchestrator_agent_id || ''
  // Use last 8 chars of adw_id or the full id if short
  return id.length > 12 ? id.slice(0, 8) + '...' : id
})

const phase = computed(() => props.event.phase ?? null)

const previewText = computed(() => {
  const text = props.event.thinking || ''
  if (text.length <= 120) return text
  return text.slice(0, 120) + '...'
})

const renderedThinking = computed(() => {
  return renderMarkdown(props.event.thinking || '')
})

const timestamp = computed(() => {
  return new Date(props.event.timestamp)
})

function formatTime(date: Date): string {
  return date.toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}
</script>

<style scoped>
.thinking-block-row {
  display: grid;
  grid-template-columns: 50px 100px 120px 1fr 120px;
  gap: var(--spacing-md);
  align-items: start;
  padding: var(--spacing-sm) var(--spacing-md);
  border-left: 8px solid rgba(6, 182, 212, 0.6);
  transition: all 0.15s ease;
  font-family: "JetBrains Mono", monospace;
  font-size: 0.875rem;
  background: rgba(6, 182, 212, 0.03);
}

.thinking-block-row:hover {
  background: rgba(6, 182, 212, 0.06);
}

.thinking-block-row.is-expanded {
  background: rgba(6, 182, 212, 0.06);
}

/* Line number */
.event-line-number {
  text-align: right;
  color: var(--text-muted);
  opacity: 0.5;
  font-size: 0.8rem;
  padding-top: 4px;
}

/* Thinking badge */
.thinking-badge {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 0.65rem;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 4px;
  background: rgba(6, 182, 212, 0.12);
  color: #22d3ee;
  border: 1px solid rgba(6, 182, 212, 0.3);
  height: fit-content;
  letter-spacing: 0.04em;
  white-space: nowrap;
}

/* Header */
.thinking-header {
  display: flex;
  flex-direction: column;
  gap: 3px;
  font-size: 0.75rem;
}

.agent-label {
  font-weight: 600;
  color: #22d3ee;
  padding: 2px 5px;
  border: 1px solid rgba(6, 182, 212, 0.4);
  border-radius: 3px;
  font-size: 0.7rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.phase-label {
  font-size: 0.6rem;
  padding: 1px 4px;
  border-radius: 3px;
  background: rgba(251, 191, 36, 0.1);
  color: #fbbf24;
  border: 1px solid rgba(251, 191, 36, 0.2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 110px;
}

/* Content */
.event-content {
  min-width: 0;
}

/* Thinking bubble */
.thinking-bubble {
  border-radius: 8px;
  border: 1px solid rgba(6, 182, 212, 0.25);
  background: rgba(6, 182, 212, 0.06);
  overflow: hidden;
  cursor: pointer;
  transition: border-color 0.15s ease;
}

.thinking-bubble:hover {
  border-color: rgba(6, 182, 212, 0.5);
}

.bubble-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 10px;
  border-bottom: 1px solid rgba(6, 182, 212, 0.15);
  background: rgba(6, 182, 212, 0.08);
}

.bubble-title {
  font-size: 0.7rem;
  font-weight: 700;
  color: #22d3ee;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}

.expand-btn {
  font-size: 0.65rem;
  color: rgba(6, 182, 212, 0.7);
  background: none;
  border: none;
  cursor: pointer;
  padding: 0;
  font-family: "JetBrains Mono", monospace;
  transition: color 0.15s ease;
}

.expand-btn:hover {
  color: #22d3ee;
}

.bubble-content {
  padding: 8px 10px;
}

.thinking-text {
  font-size: 0.8rem;
  line-height: 1.6;
  color: rgba(226, 232, 240, 0.85);
  font-style: italic;
  white-space: pre-wrap;
  word-wrap: break-word;
}

.thinking-text :deep(p) {
  margin: 0.4em 0;
}

.thinking-text :deep(p:first-child) {
  margin-top: 0;
}

.thinking-text :deep(code) {
  background: rgba(6, 182, 212, 0.1);
  padding: 1px 4px;
  border-radius: 3px;
  font-style: normal;
}

.bubble-preview {
  padding: 6px 10px;
}

.preview-text {
  font-size: 0.8rem;
  color: rgba(34, 211, 238, 0.6);
  font-style: italic;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  display: block;
}

/* Metadata */
.event-meta {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 4px;
  color: var(--text-muted);
  font-size: 0.75rem;
  padding-top: 4px;
}

.event-time {
  opacity: 0.7;
}

/* Animation */
@keyframes slideIn {
  from {
    opacity: 0;
    transform: translateX(-10px);
  }
  to {
    opacity: 1;
    transform: translateX(0);
  }
}

.thinking-block-row {
  animation: slideIn 0.2s ease-out;
}
</style>
