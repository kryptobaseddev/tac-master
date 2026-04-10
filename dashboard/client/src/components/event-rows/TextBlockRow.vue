<!--
  TextBlockRow — renders a text block event in the EventStream.

  Displays formatted text (not raw JSON) for streaming text content.
  Mapped from text block data stored in orchestratorStore.textBlocks.
-->
<template>
  <div class="text-block-row" :class="{ 'is-expanded': isExpanded }">
    <div class="event-line-number">{{ lineNumber }}</div>

    <div class="response-badge">
      RESPONSE
    </div>

    <div class="text-header">
      <span class="agent-label">{{ agentLabel }}</span>
      <span v-if="phase" class="phase-label">{{ phase }}</span>
    </div>

    <div class="event-content" @click="toggleExpanded">
      <div class="text-body">
        <div
          v-if="isExpanded || text.length <= 300"
          class="text-content"
          v-html="renderedText"
        ></div>
        <div v-else class="text-preview">
          <span class="preview-text" v-html="renderedPreview"></span>
          <span class="expand-hint">[ click to expand ]</span>
        </div>
      </div>
    </div>

    <div class="event-meta">
      <span v-if="isExpanded && text.length > 300" class="char-count">{{ text.length }} chars</span>
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
    text: string
    timestamp: number
    phase?: string
  }
  lineNumber: number
}

const props = defineProps<Props>()
const isExpanded = ref(false)

const toggleExpanded = () => {
  if (props.event.text.length > 300) {
    isExpanded.value = !isExpanded.value
  }
}

const agentLabel = computed(() => {
  const id = props.event.orchestrator_agent_id || ''
  return id.length > 12 ? id.slice(0, 8) + '...' : id
})

const phase = computed(() => props.event.phase ?? null)

const text = computed(() => props.event.text || '')

const renderedText = computed(() => renderMarkdown(text.value))

const renderedPreview = computed(() => renderMarkdown(text.value.slice(0, 300)))

const timestamp = computed(() => new Date(props.event.timestamp))

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
.text-block-row {
  display: grid;
  grid-template-columns: 50px 100px 120px 1fr 120px;
  gap: var(--spacing-md);
  align-items: start;
  padding: var(--spacing-sm) var(--spacing-md);
  border-left: 8px solid rgba(34, 197, 94, 0.6);
  transition: all 0.15s ease;
  font-family: "JetBrains Mono", monospace;
  font-size: 0.875rem;
  background: rgba(34, 197, 94, 0.02);
  cursor: pointer;
}

.text-block-row:hover {
  background: rgba(34, 197, 94, 0.05);
}

.text-block-row.is-expanded {
  background: rgba(34, 197, 94, 0.05);
}

/* Line number */
.event-line-number {
  text-align: right;
  color: var(--text-muted);
  opacity: 0.5;
  font-size: 0.8rem;
  padding-top: 4px;
}

/* Response badge */
.response-badge {
  display: flex;
  align-items: center;
  font-size: 0.65rem;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 4px;
  background: rgba(34, 197, 94, 0.12);
  color: #4ade80;
  border: 1px solid rgba(34, 197, 94, 0.3);
  height: fit-content;
  letter-spacing: 0.04em;
  white-space: nowrap;
}

/* Header */
.text-header {
  display: flex;
  flex-direction: column;
  gap: 3px;
  font-size: 0.75rem;
}

.agent-label {
  font-weight: 600;
  color: #4ade80;
  padding: 2px 5px;
  border: 1px solid rgba(34, 197, 94, 0.4);
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

.text-body {
  background: rgba(34, 197, 94, 0.05);
  border: 1px solid rgba(34, 197, 94, 0.15);
  border-radius: 6px;
  padding: 8px 10px;
}

.text-content {
  font-size: 0.82rem;
  line-height: 1.65;
  color: var(--text-primary);
  word-wrap: break-word;
}

.text-content :deep(p) {
  margin: 0.5em 0;
  line-height: 1.65;
}

.text-content :deep(p:first-child) {
  margin-top: 0;
}

.text-content :deep(p:last-child) {
  margin-bottom: 0;
}

.text-content :deep(code) {
  background: rgba(255, 255, 255, 0.08);
  padding: 2px 5px;
  border-radius: 3px;
  font-family: "JetBrains Mono", monospace;
  font-size: 0.85em;
}

.text-content :deep(pre) {
  background: rgba(0, 0, 0, 0.25);
  padding: 10px;
  border-radius: 4px;
  overflow-x: auto;
  margin: 0.75em 0;
  font-size: 0.8rem;
}

.text-content :deep(pre code) {
  background: none;
  padding: 0;
}

.text-content :deep(ul),
.text-content :deep(ol) {
  margin: 0.5em 0;
  padding-left: 18px;
}

.text-content :deep(li) {
  margin: 0.25em 0;
}

.text-content :deep(strong) {
  font-weight: 700;
}

.text-content :deep(em) {
  font-style: italic;
}

.text-content :deep(h1),
.text-content :deep(h2),
.text-content :deep(h3) {
  font-weight: 700;
  margin: 0.8em 0 0.4em;
  line-height: 1.3;
}

.text-content :deep(h1:first-child),
.text-content :deep(h2:first-child),
.text-content :deep(h3:first-child) {
  margin-top: 0;
}

.text-preview {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.preview-text {
  font-size: 0.82rem;
  color: var(--text-primary);
  line-height: 1.5;
}

.preview-text :deep(p) {
  margin: 0;
  display: inline;
}

.expand-hint {
  font-size: 0.7rem;
  color: rgba(34, 197, 94, 0.5);
  font-style: italic;
  margin-top: 2px;
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

.char-count {
  font-size: 0.65rem;
  color: rgba(34, 197, 94, 0.6);
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

.text-block-row {
  animation: slideIn 0.2s ease-out;
}
</style>
