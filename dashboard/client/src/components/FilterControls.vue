<template>
  <div class="filter-controls">
    <!-- Top row: Tabs + Level Filters + Search + Controls -->
    <div class="filter-top-row">
      <!-- Tab Filters: Combined / Errors / Performance -->
      <div class="tab-filters">
        <button
          class="tab-btn"
          :class="{ active: activeTab === 'combined' }"
          @click="$emit('tab-change', 'combined')"
          title="Combined stream — all events"
        >
          Combined
        </button>
        <button
          class="tab-btn tab-errors"
          :class="{ active: activeTab === 'errors' }"
          @click="$emit('tab-change', 'errors')"
          title="Errors only — WARNING + ERROR level events"
        >
          Errors
        </button>
        <button
          class="tab-btn tab-perf"
          :class="{ active: activeTab === 'performance' }"
          @click="$emit('tab-change', 'performance')"
          title="Performance — TOOL events showing duration and resource usage"
        >
          Performance
        </button>
      </div>

      <!-- Separator -->
      <div class="filter-sep"></div>

      <!-- Level Filters: INFO / WARN / ERROR -->
      <div class="level-filters">
        <button
          class="level-btn level-info"
          :class="{ active: levelFilterActive('INFO') }"
          @click="$emit('level-filter-toggle', 'INFO')"
          title="Show INFO level events"
        >
          INFO
        </button>
        <button
          class="level-btn level-warn"
          :class="{ active: levelFilterActive('WARNING') }"
          @click="$emit('level-filter-toggle', 'WARNING')"
          title="Show WARNING level events"
        >
          WARN
        </button>
        <button
          class="level-btn level-error"
          :class="{ active: levelFilterActive('ERROR') }"
          @click="$emit('level-filter-toggle', 'ERROR')"
          title="Show ERROR level events"
        >
          ERROR
        </button>
      </div>
    </div>

    <!-- Bottom row: Category Filters + Agent Filters + Search + Controls -->
    <div class="filter-bottom-row">
      <!-- Left side: Category + Agent filters -->
      <div class="filter-left">
        <!-- Category Filters (TOOL, RESPONSE, THINKING, HOOK) -->
        <div class="category-filters">
          <button
            v-for="filter in categoryFilters"
            :key="filter.value"
            class="quick-filter-btn"
            :class="[filter.class, { active: categoryFilterActive(filter.value) }]"
            @click="toggleCategoryFilter(filter.value)"
            :title="`Filter by ${filter.label}`"
          >
            <span class="filter-emoji">{{ getCategoryEmoji(filter.value) }}</span>
            {{ filter.label }}
          </button>
        </div>

        <!-- Active Agent Filters (prominent display) -->
        <div v-if="activeAgentFilters && activeAgentFilters.size > 0" class="active-agent-filters">
          <button
            v-for="agentName in Array.from(activeAgentFilters)"
            :key="agentName"
            class="active-agent-filter-btn"
            :style="{ borderColor: getAgentBorderColorForName(agentName) }"
            @click="toggleAgentFilter(agentName)"
            :title="`Click to clear filter for ${agentName}`"
          >
            <span class="filter-prefix">AGENT:</span>
            <span class="filter-name">{{ agentName }}</span>
            <span class="filter-remove">x</span>
          </button>
        </div>
      </div>

      <!-- Right side: Search and Controls -->
      <div class="filter-right">
        <!-- Search Input -->
        <input
          :value="searchQuery"
          @input="$emit('update:searchQuery', ($event.target as HTMLInputElement).value)"
          type="text"
          placeholder="Search events, agents, tools (regex)"
          class="search-input"
          title="Search across: agent name, content, event type, summary, task ID, session ID, file paths. Supports regex."
        />

        <!-- Clear All Button -->
        <button
          class="control-btn clear-all"
          @click="$emit('clear-all')"
          title="Clear all events from the stream"
        >
          Clear All
        </button>

        <!-- Auto-Follow Button -->
        <button
          class="control-btn auto-follow"
          :class="{ active: autoScroll }"
          @click="toggleAutoScroll"
          title="Auto-scroll to bottom when new events arrive"
        >
          Auto-Follow
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { QuickFilter, AgentFilter } from '../composables/useEventStreamFilter'
import { useOrchestratorStore } from '../stores/orchestratorStore'
import { getAgentBorderColor } from '../utils/agentColors'

interface Props {
  categoryFilters: QuickFilter[]
  quickFilters?: QuickFilter[]
  agentFilters: AgentFilter[]
  toolFilters?: AgentFilter[]
  searchQuery: string
  autoScroll: boolean
  activeAgentFilters?: Set<string>
  activeCategoryFilters?: Set<string>
  activeQuickFilters?: Set<string>
  activeToolFilters?: Set<string>
  activeLevelFilters?: Set<string>
  activeTab?: string
}

interface Emits {
  'update:searchQuery': [value: string]
  'update:autoScroll': [value: boolean]
  'agent-filter-toggle': [value: string]
  'category-filter-toggle': [value: string]
  'level-filter-toggle': [value: string]
  'tab-change': [value: string]
  'auto-scroll-toggle': []
  'clear-all': []
}

const props = withDefaults(defineProps<Props>(), {
  quickFilters: () => [],
  toolFilters: () => [],
  activeAgentFilters: () => new Set(),
  activeCategoryFilters: () => new Set(),
  activeQuickFilters: () => new Set(),
  activeToolFilters: () => new Set(),
  activeLevelFilters: () => new Set(),
  activeTab: 'combined'
})
const emit = defineEmits<Emits>()

// Store access for agent data
const store = useOrchestratorStore()

// Get agent border color by agent name
const getAgentBorderColorForName = (agentName: string): string => {
  const agent = store.agents.find(a => a.name === agentName)
  if (agent) {
    return getAgentBorderColor(agent.name, agent.id)
  }
  return getAgentBorderColor(agentName, agentName)
}

const agentFilterActive = (value: string): boolean => {
  return props.activeAgentFilters?.has(value) ?? false
}

const categoryFilterActive = (value: string): boolean => {
  return props.activeCategoryFilters?.has(value) ?? false
}

const levelFilterActive = (value: string): boolean => {
  return props.activeLevelFilters?.has(value) ?? false
}

const toggleAgentFilter = (value: string) => {
  emit('agent-filter-toggle', value)
}

const toggleCategoryFilter = (value: string) => {
  emit('category-filter-toggle', value)
}

const toggleAutoScroll = () => {
  emit('update:autoScroll', !props.autoScroll)
  emit('auto-scroll-toggle')
}

const getCategoryEmoji = (category: string): string => {
  const emojiMap: Record<string, string> = {
    'TOOL': '\u{1F6E0}',
    'RESPONSE': '\u{1F4AC}',
    'THINKING': '\u{1F9E0}',
    'HOOK': '\u{1FA9D}'
  }
  return emojiMap[category] || '\u{1F4CB}'
}
</script>

<style scoped>
/* Filter Controls Container */
.filter-controls {
  display: flex;
  flex-direction: column;
  background: var(--bg-secondary);
  border-bottom: 1px solid var(--border-color);
}

/* Top row: tabs + level filters */
.filter-top-row {
  display: flex;
  align-items: center;
  padding: var(--spacing-sm) var(--spacing-md);
  gap: var(--spacing-md);
  border-bottom: 1px solid var(--border-color);
  background: var(--bg-primary);
}

/* Bottom row: category + search */
.filter-bottom-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: var(--spacing-sm) var(--spacing-md);
  gap: var(--spacing-md);
}

/* Separator */
.filter-sep {
  width: 1px;
  height: 20px;
  background: var(--border-color);
  flex-shrink: 0;
}

/* Tab Filters */
.tab-filters {
  display: flex;
  gap: 2px;
  align-items: center;
}

.tab-btn {
  padding: 0.3rem 0.75rem;
  font-size: 0.75rem;
  font-weight: 700;
  border-radius: 4px;
  border: 1px solid var(--border-color);
  cursor: pointer;
  transition: all 0.2s ease;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  background: var(--bg-secondary);
  color: var(--text-muted);
}

.tab-btn:hover {
  color: var(--text-primary);
  border-color: var(--accent-primary);
}

.tab-btn.active {
  background: rgba(6, 182, 212, 0.15);
  color: var(--accent-primary);
  border-color: rgba(6, 182, 212, 0.4);
}

.tab-btn.tab-errors:hover,
.tab-btn.tab-errors.active {
  background: rgba(239, 68, 68, 0.15);
  color: #ef4444;
  border-color: rgba(239, 68, 68, 0.4);
}

.tab-btn.tab-perf:hover,
.tab-btn.tab-perf.active {
  background: rgba(251, 146, 60, 0.15);
  color: #fb923c;
  border-color: rgba(251, 146, 60, 0.4);
}

/* Level Filters */
.level-filters {
  display: flex;
  gap: 3px;
  align-items: center;
}

.level-btn {
  padding: 0.3rem 0.6rem;
  font-size: 0.7rem;
  font-weight: 800;
  border-radius: 3px;
  border: 1px solid transparent;
  cursor: pointer;
  transition: all 0.2s ease;
  letter-spacing: 0.05em;
}

.level-btn.level-info {
  background: rgba(59, 130, 246, 0.1);
  color: #60a5fa;
  border-color: rgba(59, 130, 246, 0.25);
}

.level-btn.level-info:hover,
.level-btn.level-info.active {
  background: rgba(59, 130, 246, 0.25);
  color: #93c5fd;
  border-color: rgba(59, 130, 246, 0.5);
}

.level-btn.level-info.active {
  background: #3b82f6;
  color: white;
}

.level-btn.level-warn {
  background: rgba(251, 191, 36, 0.1);
  color: #fbbf24;
  border-color: rgba(251, 191, 36, 0.25);
}

.level-btn.level-warn:hover,
.level-btn.level-warn.active {
  background: rgba(251, 191, 36, 0.25);
  color: #fde68a;
  border-color: rgba(251, 191, 36, 0.5);
}

.level-btn.level-warn.active {
  background: #d97706;
  color: white;
}

.level-btn.level-error {
  background: rgba(239, 68, 68, 0.1);
  color: #f87171;
  border-color: rgba(239, 68, 68, 0.25);
}

.level-btn.level-error:hover,
.level-btn.level-error.active {
  background: rgba(239, 68, 68, 0.25);
  color: #fca5a5;
  border-color: rgba(239, 68, 68, 0.5);
}

.level-btn.level-error.active {
  background: #ef4444;
  color: white;
}

/* Left side container */
.filter-left {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex: 1;
  flex-wrap: wrap;
}

/* Filter Right Side */
.filter-right {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
  flex-shrink: 0;
}

/* Search Input */
.search-input {
  width: 240px;
  padding: 0.4rem 0.75rem;
  font-size: 0.8125rem;
  background: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: 6px;
  color: var(--text-primary);
  transition: all 0.2s ease;
}

.search-input:focus {
  border-color: var(--accent-primary);
  box-shadow: 0 0 0 2px rgba(6, 182, 212, 0.1);
  outline: none;
}

.search-input::placeholder {
  color: var(--text-muted);
  font-size: 0.8125rem;
}

/* Category Filters */
.category-filters {
  display: flex;
  gap: 4px;
  align-items: center;
}

.quick-filter-btn {
  padding: 0.3rem 0.6rem;
  font-size: 0.7rem;
  font-weight: 700;
  border-radius: 4px;
  border: 1px solid transparent;
  cursor: pointer;
  transition: all 0.2s ease;
  text-transform: uppercase;
  letter-spacing: 0.025em;
  display: flex;
  align-items: center;
  gap: 3px;
}

.quick-filter-btn:hover {
  transform: translateY(-1px);
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.3);
}

/* Filter Emoji */
.filter-emoji {
  font-size: 0.85em;
}

.qf-tool {
  background: rgba(251, 146, 60, 0.15);
  color: #fb923c;
  border-color: rgba(251, 146, 60, 0.3);
}

.qf-tool.active {
  background: #fb923c;
  color: white;
}

.qf-response {
  background: rgba(34, 197, 94, 0.15);
  color: var(--status-success);
  border-color: rgba(34, 197, 94, 0.3);
}

.qf-response.active {
  background: var(--status-success);
  color: white;
}

.qf-thinking {
  background: rgba(168, 85, 247, 0.15);
  color: #a855f7;
  border-color: rgba(168, 85, 247, 0.3);
}

.qf-thinking.active {
  background: #a855f7;
  color: white;
}

.qf-hook {
  background: rgba(6, 182, 212, 0.15);
  color: var(--accent);
  border-color: rgba(6, 182, 212, 0.3);
}

.qf-hook.active {
  background: var(--accent);
  color: white;
}

/* Active Agent Filters (prominent display) */
.active-agent-filters {
  display: flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
}

.active-agent-filter-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0.3rem 0.6rem;
  font-size: 0.7rem;
  font-weight: 700;
  border-radius: 4px;
  border: 2px solid;
  cursor: pointer;
  transition: all 0.2s ease;
  background: #0d0f1a;
  color: white;
  text-transform: uppercase;
  letter-spacing: 0.025em;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.4);
}

.active-agent-filter-btn:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.6);
  background: #13152a;
  filter: brightness(1.05);
}

.filter-prefix {
  font-size: 0.65rem;
  opacity: 0.7;
  font-weight: 600;
  color: #9ca3af;
}

.filter-name {
  font-size: 0.7rem;
  font-weight: 700;
  color: #e5e7eb;
}

.filter-remove {
  font-size: 0.8rem;
  opacity: 0.6;
  margin-left: 2px;
  color: #9ca3af;
}

.active-agent-filter-btn:hover .filter-remove {
  opacity: 0.9;
  color: #d1d5db;
}

/* Control Buttons */
.control-btn {
  padding: 0.3rem 0.6rem;
  font-size: 0.7rem;
  font-weight: 700;
  border-radius: 4px;
  border: 1px solid transparent;
  cursor: pointer;
  transition: all 0.2s ease;
  text-transform: uppercase;
  letter-spacing: 0.025em;
}

.control-btn.auto-follow {
  background: rgba(6, 182, 212, 0.15);
  color: var(--accent-primary);
  border-color: rgba(6, 182, 212, 0.3);
}

.control-btn.auto-follow:hover {
  transform: translateY(-1px);
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.3);
}

.control-btn.auto-follow.active {
  background: var(--accent-primary);
  color: white;
}

/* Clear All Button */
.control-btn.clear-all {
  background: rgba(239, 68, 68, 0.15);
  color: #ef4444;
  border-color: rgba(239, 68, 68, 0.3);
}

.control-btn.clear-all:hover {
  background: #ef4444;
  color: white;
  transform: translateY(-1px);
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.3);
}
</style>
