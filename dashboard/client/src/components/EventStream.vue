<template>
  <div class="event-stream">
    <!-- Filter Controls Component -->
    <FilterControls
      :category-filters="categoryFilters"
      :quick-filters="quickFilters"
      :agent-filters="agentFilters"
      :tool-filters="toolFilters"
      :search-query="searchQuery"
      :auto-scroll="autoScroll"
      :active-quick-filters="activeQuickFilters"
      :active-agent-filters="activeAgentFilters"
      :active-category-filters="activeCategoryFilters"
      :active-tool-filters="activeToolFilters"
      :active-level-filters="activeLevelFilters"
      :active-tab="activeTab"
      @quick-filter-toggle="toggleQuickFilter"
      @agent-filter-toggle="toggleAgentFilter"
      @category-filter-toggle="toggleCategoryFilter"
      @tool-filter-toggle="toggleToolFilter"
      @level-filter-toggle="toggleLevelFilter"
      @tab-change="setTab"
      @update:search-query="searchQuery = $event"
      @auto-scroll-toggle="store.toggleAutoScroll"
      @clear-all="store.clearEventStream"
    />

    <div class="event-stream-content" ref="streamRef">
      <!-- Empty State -->
      <div v-if="combinedFilteredEntries.length === 0" class="empty-state">
        <div class="empty-icon">
          <svg
            width="98"
            height="98"
            viewBox="0 0 98 98"
            xmlns="http://www.w3.org/2000/svg"
          >
            <!-- 3x3 grid of rounded rectangles -->
            <!-- Row 1 -->
            <rect x="0" y="0" width="30" height="30" rx="2" fill="#000" stroke="#404040" stroke-width="1"/>
            <rect x="34" y="0" width="30" height="30" rx="2" fill="#000" stroke="#404040" stroke-width="1"/>
            <rect x="68" y="0" width="30" height="30" rx="2" fill="#000" stroke="#404040" stroke-width="1"/>

            <!-- Row 2 -->
            <rect x="0" y="34" width="30" height="30" rx="2" fill="#000" stroke="#404040" stroke-width="1"/>
            <rect x="34" y="34" width="30" height="30" rx="2" fill="#000" stroke="#404040" stroke-width="1"/>
            <rect x="68" y="34" width="30" height="30" rx="2" fill="#000" stroke="#404040" stroke-width="1"/>

            <!-- Row 3 -->
            <rect x="0" y="68" width="30" height="30" rx="2" fill="#000" stroke="#404040" stroke-width="1"/>
            <rect x="34" y="68" width="30" height="30" rx="2" fill="#000" stroke="#404040" stroke-width="1"/>
            <rect x="68" y="68" width="30" height="30" rx="2" fill="#000" stroke="#404040" stroke-width="1"/>
          </svg>
        </div>
        <p class="empty-title">
          {{
            searchQuery
              ? "No events match your search"
              : "No events yet. Waiting for agent activity..."
          }}
        </p>
      </div>

      <!-- Event Items (combined: hook events + streaming blocks) -->
      <div v-else class="event-items">
        <template v-for="entry in combinedFilteredEntries" :key="entry.id">
          <!-- Streaming: ThinkingBlock -->
          <ThinkingBlockRow
            v-if="entry.renderType === 'thinking_block'"
            :event="(entry.data as any)"
            :line-number="entry.lineNumber"
          />

          <!-- Streaming: TextBlock -->
          <TextBlockRow
            v-else-if="entry.renderType === 'text_block'"
            :event="(entry.data as any)"
            :line-number="entry.lineNumber"
          />

          <!-- Streaming: ToolUseBlock (reuse AgentToolUseBlockRow with adapted data) -->
          <AgentToolUseBlockRow
            v-else-if="entry.renderType === 'tool_use_block'"
            :event="(entry.data as any)"
            :line-number="entry.lineNumber"
          />

          <!-- Hook events: use existing component routing -->
          <template v-else-if="entry.renderType === 'event_stream' && entry.eventStreamEntry">
            <component
              v-if="getEventComponent(entry.eventStreamEntry)"
              :is="getEventComponent(entry.eventStreamEntry)"
              :event="getEventData(entry.eventStreamEntry)"
              :line-number="entry.eventStreamEntry.lineNumber"
            />
          </template>
        </template>
      </div>

      <!-- Auto-scroll anchor -->
      <div ref="bottomRef"></div>
    </div>
  </div>
</template>

<script setup lang="ts">
import {
  computed,
  watch,
  nextTick,
  onMounted,
  ref,
  type ComputedRef,
} from "vue";
import { useOrchestratorStore } from "../stores/orchestratorStore";
import { useEventStreamFilter } from "../composables/useEventStreamFilter";
import FilterControls from "./FilterControls.vue";
import AgentLogRow from "./event-rows/AgentLogRow.vue";
import AgentToolUseBlockRow from "./event-rows/AgentToolUseBlockRow.vue";
import ThinkingBlockRow from "./event-rows/ThinkingBlockRow.vue";
import TextBlockRow from "./event-rows/TextBlockRow.vue";
import type { EventStreamEntry } from "../types";

// Store
const store = useOrchestratorStore();

// Get events from store (full array, not pre-filtered)
// The useEventStreamFilter composable handles agent filtering via activeAgentFilters
const events: ComputedRef<EventStreamEntry[]> = computed(
  () => store.eventStreamEntries
);

// Use filter composable (without autoScroll, which is now in store)
const {
  activeTab,
  activeAgentFilters,
  activeCategoryFilters,
  activeToolFilters,
  activeQuickFilters,
  activeLevelFilters,
  searchQuery,
  categoryFilters,
  quickFilters,
  agentFilters,
  toolFilters,
  filteredEvents,
  setTab,
  toggleQuickFilter,
  toggleAgentFilter,
  toggleCategoryFilter,
  toggleToolFilter,
  toggleLevelFilter,
  clearAllFilters,
} = useEventStreamFilter(() => events.value);

// Get autoScroll from store (shared with OrchestratorChat)
const autoScroll = computed(() => store.autoScroll);

// ---------------------------------------------------------------------------
// Combined stream: merge hook events + streaming blocks sorted by timestamp
// ---------------------------------------------------------------------------

interface CombinedEntry {
  id: string
  lineNumber: number
  timestamp: number
  renderType: 'event_stream' | 'thinking_block' | 'text_block' | 'tool_use_block'
  // For event_stream type
  eventStreamEntry?: EventStreamEntry
  // For streaming block types
  data?: Record<string, unknown>
}

/**
 * Build a unified timeline of hook events and streaming blocks.
 * Thinking blocks render as collapsible cyan bubbles.
 * Text blocks render as formatted markdown.
 * Tool use blocks reuse AgentToolUseBlockRow with adapted data.
 */
const combinedEntries = computed<CombinedEntry[]>(() => {
  const entries: CombinedEntry[] = []

  // 1. Hook events from filteredEvents (already filtered by agent/category/search)
  filteredEvents.value.forEach((entry) => {
    // Skip entries that render null
    if (!getEventComponent(entry)) return
    const ts = entry.timestamp instanceof Date
      ? entry.timestamp.getTime()
      : new Date(entry.timestamp as string).getTime()
    entries.push({
      id: entry.id,
      lineNumber: entry.lineNumber,
      timestamp: ts,
      renderType: 'event_stream',
      eventStreamEntry: entry,
    })
  })

  // 2. Thinking blocks — only if not filtered out by tab or category
  const showThinking = shouldShowBlockType('thinking')
  if (showThinking) {
    store.thinkingBlocks.forEach((block) => {
      if (!matchesSearch(block.thinking)) return
      if (!matchesLevelFilters('INFO')) return
      entries.push({
        id: `thinking-${block.id}`,
        lineNumber: 0, // will be reassigned
        timestamp: block.timestamp,
        renderType: 'thinking_block',
        data: block as unknown as Record<string, unknown>,
      })
    })
  }

  // 3. Text blocks — only if not filtered out by tab or category
  const showText = shouldShowBlockType('text')
  if (showText) {
    store.textBlocks.forEach((block) => {
      if (!matchesSearch(block.text)) return
      if (!matchesLevelFilters('INFO')) return
      entries.push({
        id: `text-${block.id}`,
        lineNumber: 0,
        timestamp: block.timestamp,
        renderType: 'text_block',
        data: block as unknown as Record<string, unknown>,
      })
    })
  }

  // 4. Tool use blocks — only if not filtered out by tab or category
  const showToolUse = shouldShowBlockType('tool_use')
  if (showToolUse) {
    store.toolUseBlocks.forEach((block) => {
      if (!matchesSearch(block.tool_name)) return
      if (!matchesLevelFilters('INFO')) return
      // Adapt to AgentLog-like shape for AgentToolUseBlockRow
      entries.push({
        id: `tooluse-${block.id}`,
        lineNumber: 0,
        timestamp: block.timestamp,
        renderType: 'tool_use_block',
        data: {
          id: block.id,
          agent_id: block.orchestrator_agent_id,
          agent_name: block.orchestrator_agent_id,
          session_id: null,
          task_slug: null,
          entry_index: null,
          event_category: 'tool',
          event_type: 'tool_use',
          content: `Tool: ${block.tool_name}`,
          payload: {
            tool_name: block.tool_name,
            tool_input: block.tool_input,
          },
          summary: `Tool: ${block.tool_name}`,
          timestamp: new Date(block.timestamp).toISOString(),
        } as unknown as Record<string, unknown>,
      })
    })
  }

  // Sort by timestamp ascending and reassign line numbers
  entries.sort((a, b) => a.timestamp - b.timestamp)
  entries.forEach((e, i) => {
    e.lineNumber = i + 1
  })

  return entries
})

/**
 * Apply additional tab/level filter pass to the combined entries.
 * (Streaming blocks already checked above, hook events go through filteredEvents.)
 */
const combinedFilteredEntries = computed<CombinedEntry[]>(() => {
  return combinedEntries.value
})

/**
 * Determine if a given block type should be shown based on tab and category filters.
 */
function shouldShowBlockType(blockType: 'thinking' | 'text' | 'tool_use'): boolean {
  // Tab-based exclusion
  if (activeTab.value === 'errors') return false // Streaming blocks are not error events
  if (activeTab.value === 'performance') {
    return blockType === 'tool_use'
  }

  // Category filter exclusion
  if (activeCategoryFilters.value.size > 0) {
    if (blockType === 'thinking' && !activeCategoryFilters.value.has('THINKING')) return false
    if (blockType === 'text' && !activeCategoryFilters.value.has('RESPONSE')) return false
    if (blockType === 'tool_use' && !activeCategoryFilters.value.has('TOOL')) return false
  }

  return true
}

/**
 * Check if content matches the current search query (regex or plain).
 */
function matchesSearch(content: string): boolean {
  if (!searchQuery.value.trim()) return true
  const query = searchQuery.value.toLowerCase()
  try {
    const regex = new RegExp(query, 'i')
    return regex.test(content)
  } catch {
    return content.toLowerCase().includes(query)
  }
}

/**
 * Check if a given level passes the active level filters.
 * Returns true when no level filters are set.
 */
function matchesLevelFilters(level: string): boolean {
  if (activeLevelFilters.value.size === 0) return true
  return activeLevelFilters.value.has(level)
}

// Get appropriate component for event type
function getEventComponent(event: EventStreamEntry) {
  // Check for special block types first (for the orchestrator, no need to display this here its in the chat)
  if (event.sourceType === "thinking_block") {
    // Skip — thinking blocks from eventStreamEntries rendered via streaming path
    return null;
  }
  // Skip tool_use_block from eventStreamEntries — rendered via streaming path
  if (event.sourceType === "tool_use_block") {
    return null;
  }
  // SKIP: orchestrator_chat messages in event stream for searchability
  if (event.sourceType === "orchestrator_chat") {
    return null; // Don't display in event stream, only in chat
  }

  // Then check standard types
  switch (event.sourceType) {
    case "agent_log":
      // Check if this is an agent tool use event
      const eventType = event.eventType?.toLowerCase();
      if (eventType === "tool_use" || eventType === "tooluseblock") {
        return AgentToolUseBlockRow;
      }
      return AgentLogRow;
    default:
      return AgentLogRow; // Fallback
  }
}

// Get event data in correct format for component
function getEventData(event: EventStreamEntry) {
  // For thinking_block events
  if (event.sourceType === "thinking_block") {
    return event.metadata?.data || event;
  }

  // For tool_use_block events
  if (event.sourceType === "tool_use_block") {
    return event.metadata?.data || event;
  }

  // For orchestrator_chat events, pass the metadata which contains all needed fields
  if (event.sourceType === "orchestrator_chat") {
    return {
      message: event.content,
      sender_type: event.metadata?.sender_type || "user",
      receiver_type: event.metadata?.receiver_type || "orchestrator",
      agent_id: event.metadata?.agent_id,
      orchestrator_agent_id: event.metadata?.orchestrator_agent_id,
      timestamp: event.timestamp,
      created_at: event.timestamp,
      id: event.id,
      metadata: event.metadata,
    };
  }
  // EventStreamEntry metadata contains the original AgentLog for other types.
  // T033: merge EventStreamEntry.metadata (phase, repo_url, adw_id) into the
  // AgentLog so AgentLogRow can render phase badges and repo chips per row.
  const originalEvent = event.metadata?.originalEvent || event;
  if (event.metadata && originalEvent !== event) {
    return {
      ...originalEvent,
      metadata: {
        ...(originalEvent.metadata ?? {}),
        phase: event.metadata.phase ?? originalEvent.metadata?.phase,
        repo_url: event.metadata.repo_url ?? originalEvent.metadata?.repo_url,
        adw_id: event.metadata.adw_id ?? originalEvent.metadata?.adw_id,
      },
    };
  }
  return originalEvent;
}

// Refs
const streamRef = ref<HTMLElement>();
const bottomRef = ref<HTMLElement>();

// Auto-scroll to bottom when new events arrive or filters change
watch(
  () => combinedFilteredEntries.value.length,
  async () => {
    if (autoScroll.value) {
      await nextTick();
      bottomRef.value?.scrollIntoView({ behavior: "smooth" });
    }
  }
);

// Load event history on mount
onMounted(async () => {
  // Events are loaded in store.initialize()
  // This component just displays them

  // Scroll to bottom after initial render
  await nextTick();
  if (autoScroll.value && bottomRef.value) {
    bottomRef.value.scrollIntoView({ behavior: "auto" });
  }
});

// Expose methods for parent components
defineExpose({
  toggleAgentFilter,
  clearAllFilters,
});
</script>

<style scoped>
.event-stream {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--bg-primary);
}

.event-stream-content {
  flex: 1;
  overflow-y: auto;
  padding: var(--spacing-md);
  font-family: var(--font-mono);
  font-size: 0.8125rem;
  line-height: 1.6;
}

/* Empty State */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  text-align: center;
  color: var(--text-muted);
}

.empty-icon {
  width: 98px;
  height: 98px;
  margin-bottom: var(--spacing-md);
  opacity: 0.3;
  display: flex;
  align-items: center;
  justify-content: center;
}

.empty-title {
  font-size: 0.875rem;
}

/* Event Items */
.event-items {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.event-item {
  display: grid;
  grid-template-columns: 50px 80px 100px 1fr 180px;
  gap: var(--spacing-md);
  align-items: baseline;
  padding: var(--spacing-sm) var(--spacing-md);
  background: var(--bg-secondary);
  border-left: 3px solid transparent;
  transition: all 0.15s ease;
}

.event-item:hover {
  background: var(--bg-tertiary);
}

/* Event Level Styling */
.event-info {
  border-left-color: var(--status-info);
}

.event-debug {
  border-left-color: var(--status-debug);
}

.event-success {
  border-left-color: var(--status-success);
}

.event-warn {
  border-left-color: var(--status-warning);
}

.event-error {
  border-left-color: var(--status-error);
  background: rgba(239, 68, 68, 0.05);
}

.event-line-number {
  font-size: 0.75rem;
  color: var(--text-dim);
  text-align: right;
}

.event-badge {
  font-size: 0.75rem;
  font-weight: 700;
  letter-spacing: 0.025em;
}

.badge-info {
  color: var(--status-info);
}

.badge-debug {
  color: var(--status-debug);
}

.badge-success {
  color: var(--status-success);
}

.badge-warn {
  color: var(--status-warning);
}

.badge-error {
  color: var(--status-error);
}

.event-agent {
  font-size: 0.75rem;
  color: var(--agent-active);
  font-weight: 600;
}

.event-content {
  color: var(--text-primary);
  word-wrap: break-word;
}

.event-meta {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
  justify-content: flex-end;
  font-size: 0.75rem;
  color: var(--text-muted);
}

.event-tokens {
  color: var(--status-warning);
}

.event-time {
  color: var(--text-dim);
}

/* Responsive adjustments */
@media (max-width: 1200px) {
  .event-item {
    grid-template-columns: 40px 70px 80px 1fr 150px;
    gap: var(--spacing-sm);
  }
}
</style>
