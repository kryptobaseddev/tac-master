# T061 Dashboard Component Inventory

**Task**: T117  
**Date**: 2026-04-10  
**Author**: Team Lead subagent  
**Status**: COMPLETE

---

## Summary

This audit inventories all Vue components in both the tac-master dashboard and the
orchestrator_3_stream reference frontend, assigning a keep/port/remove decision to
each, and documents the full component dependency graph.

---

## 1. tac-master Components — Keep / Port / Remove

> Root: `tac-master/dashboard/client/src/`

### 1.1 Top-level Components

| File | What It Does | Store / Composable | Key Imports | Decision | Rationale |
|------|-------------|-------------------|-------------|----------|-----------|
| `App.vue` | Root entry — mounts CommandCenterLayout, wires all panels into slot templates, owns `activeTab` + `currentRepoUrl` state, calls `store.loadInitial()` on mount | `useOrchestratorStore` | CommandCenterLayout, HeaderBar, RepoSidebar, StatusBar, ActiveAgentsPanel, PipelineFlow, IssueDetails, DependencyGraph, LiveExecutionPanel, EpicTaskTree, OperatorLog, PhaseDetailModal, SystemLogs, CommandBar, Toast, RepoBoard, ConfigPage | KEEP | Root orchestrator; required by all other components |
| `ActiveAgentsPanel.vue` | Agents sidebar panel — shows all known runs (up to 10 most-recent). Status dots: green=running, yellow=stalled (>5 min idle), gray=done, red=failed. Clickable to select agent. Derives CLEO task badge from `issue_number` metadata | `useOrchestratorStore` (`recentAgents`, `runningAgents`, `eventStreamEntries`, `selectAgent`, `selectedAgentId`) | `types/Agent` | KEEP | tac-master-native, no reference equivalent. Working, feature-complete (T044) |
| `AgentList.vue` | Original reference agent sidebar — full agent cards with context-window bar, log counters (RESPONSE/TOOL/HOOK/THINKING), cost, collapsible compact mode, pulse animation | `useOrchestratorStore` (`eventStreamEntries`, `isAgentPulsing`) | `utils/agentColors`, `RoleBadge`, `utils/inferRole`, `types/Agent` | KEEP | Ported from reference with T033 additions (RoleBadge, HOOK counter fix). Currently superseded by ActiveAgentsPanel in tac-master App.vue slots but still a valid component; can be reused if a more detailed view is needed |
| `AppHeader.vue` | Reference header — connection status, stats pills (Active/Running/Logs/WS Events/Cost), view-mode switcher (LOGS/ADWS), Cmd+K PROMPT button | `useOrchestratorStore` (`isConnected`, `viewMode`, `setViewMode`, `toggleViewMode`, `commandInputVisible`, `toggleCommandInput`, `runningAdws`), `useHeaderBar` composable | — | REMOVE | **Orphan — not imported anywhere in tac-master App.vue.** IDENTICAL to orchestrator_3_stream reference. Contains ADW view mode which is reference-specific. tac-master uses `HeaderBar.vue` instead |
| `CommandBar.vue` | Operator command input bar (T053) — floating input at bottom of screen for system/agent commands. Debounced submit, keyboard shortcuts, command history | `useToastStore` | — | KEEP | tac-master-native (T053), no reference equivalent. Working |
| `CommandCenterLayout.vue` | 5-slot grid shell — header / sidebar / main / right-panel / statusbar with drag-resize splitters. Tab navigation (Dashboard/Repos/Config/Logs) | `useOrchestratorStore` (`isConnected`, `repos`) | — (pure layout, uses named slots) | KEEP | tac-master-native (T037/T047), critical infrastructure. No reference equivalent |
| `ConfigPage.vue` | Config tabs page — tabs for Repos, Budgets, Policies, ModelPrices | `api` (from `api.ts`) | `config/ReposConfigTab`, `config/BudgetsConfigTab`, `config/PoliciesConfigTab`, `config/ModelPricesConfigTab` | KEEP | tac-master-native, working |
| `DependencyGraph.vue` | D3-based CLEO task dependency graph — visualises epic/task relationships | `useCleoStore` (`tasks`, `epics`) | — | KEEP | tac-master-native (CLEO integration), no reference equivalent |
| `EventStream.vue` | Center event log — virtual-scroll list of all agent events, wired to `useEventStreamFilter` composable for multi-axis filtering | `useOrchestratorStore` (`eventStreamEntries`, `toggleAutoScroll`, `clearEventStream`), `useEventStreamFilter` composable | `FilterControls`, `event-rows/AgentLogRow`, `event-rows/AgentToolUseBlockRow`, `types/EventStreamEntry` | KEEP | Already correctly uses `store.eventStreamEntries` (full stream — filter bug pre-fixed). Category/level filters are functional |
| `FilterControls.vue` | Reusable filter bar — Quick/Combined/Errors/Performance tabs, agent chips, category toggles, tool filters, search | `useOrchestratorStore` (`agents`), `useEventStreamFilter` types | `utils/agentColors` | KEEP | Already ported from reference, working |
| `HeaderBar.vue` | tac-master command-center nav bar — WebSocket dot, Dashboard/Repos/Config tab nav, repo selector dropdown | `useOrchestratorStore` (`isConnected`, `repos`) | — (props/emits for activeTab, currentRepoUrl) | KEEP | tac-master-native (T037), replaces reference AppHeader.vue |
| `IssueDetails.vue` | GitHub issue details pane — shows issue title, body, labels for selected run | `useOrchestratorStore` (`selectedRun`) | `types/RunSummary` | KEEP | tac-master-native, working |
| `LiveExecutionPanel.vue` | Right panel — real-time stream events for the selected run; PITER accordion (phases), hook events, agent log tail | `useOrchestratorStore` (`eventStreamEntries`, `selectedAgentId`) | `types/EventStreamEntry` | KEEP | tac-master-native, high-value, no reference equivalent |
| `OperatorLog.vue` | Left sidebar operator log feed — live CLEO task/epic activity feed | `useCleoStore` | — | KEEP | tac-master-native, working |
| `PhaseDetailModal.vue` | Phase event detail modal — shown when user clicks on a PITER phase; displays full phase metadata | Props/emits only (no store) | — | KEEP | tac-master-native (T055), working |
| `PipelineFlow.vue` | PITER pipeline visualization — swimlane-style phase timeline for selected run | `useOrchestratorStore` (`selectedRun`, `repos`) | `types/RunSummary` | KEEP | tac-master-native, working |
| `RepoBoard.vue` | Repos tab — card grid of all tracked repos with status badges | `useOrchestratorStore` (`repos`, `agents`) | `types/RepoStatus, Agent` | KEEP | tac-master-native, working |
| `RepoSidebar.vue` | Left sidebar repo list — repo navigation links | Props only (receives `epics: EpicSummary[]`, `tasks: TaskSummary[]` types from cleoStore) | — | KEEP | tac-master-native, working |
| `RoleBadge.vue` | Small agent role badge chip — coloured pill for Worker/Planner/Reviewer/Orchestrator etc. | None (pure display) | — | KEEP | tac-master-native (T033), reused by AgentList, RunDetailsPanel |
| `RunDetailsPanel.vue` | Run detail panel — shows selected agent's summary, role, cost, token count | `useOrchestratorStore` (`selectedRun`, `selectedAgent`) | `types/EventStreamEntry, Agent`, `RoleBadge`, `utils/inferRole` | KEEP | tac-master-native, working |
| `StatusBar.vue` | Bottom KPI bar — total cost, active agents, event count, WS reconnect status | `useOrchestratorStore` (`stats`, `isConnected`) | — | KEEP | tac-master-native, working |
| `SystemLogs.vue` | System logs tab — raw system-level log stream | No store (standalone fetch on mount) | — | KEEP | tac-master-native, working |
| `Toast.vue` | T053 toast notifications — floating dismissible toast stack | `useToastStore` | — | KEEP | tac-master-native (T053), working |

### 1.2 chat/ Sub-components

| File | What It Does | Store / Composable | Key Imports | Decision | Rationale |
|------|-------------|-------------------|-------------|----------|-----------|
| `chat/ThinkingBubble.vue` | Expandable thinking-block card — collapsible thinking summary with markdown render | None | `utils/markdown` | KEEP | Identical to reference, working |
| `chat/ToolUseBubble.vue` | Tool call card — displays tool name, input/output JSON in expand/collapse blocks | None (pure display) | — | KEEP | Identical to reference, working |

### 1.3 event-rows/ Sub-components

| File | What It Does | Store / Composable | Key Imports | Decision | Rationale |
|------|-------------|-------------------|-------------|----------|-----------|
| `event-rows/AgentLogRow.vue` | Hook/agent event row — renders text, thinking, tool_use events. T033 additions: phase badge, repo chip, adw_id merging from EventStreamEntry.metadata | `useOrchestratorStore` (`fileTrackingEvents`) | `types`, `utils/agentColors`, `utils/markdown`, `event-rows/FileChangesDisplay` | KEEP | Adapted from reference (T033 metadata enrichment), working |
| `event-rows/AgentToolUseBlockRow.vue` | Tool use event row (detailed) — full tool call with formatted input/output | None | `types/AgentLog`, `utils/agentColors` | KEEP | Identical to reference, working |
| `event-rows/FileChangesDisplay.vue` | File diff summary — shows changed/read file list with IDE-open link | None | `types/FileChange, FileRead`, `services/fileService` | KEEP | Identical to reference, working |
| `event-rows/OrchestratorChatRow.vue` | Chat message in event stream — renders chat entries with markdown | None | `types/OrchestratorChat`, `utils/markdown` | KEEP | Identical to reference, working |
| `event-rows/SystemLogRow.vue` | System-level log row — minimal display for system events | None | `types/SystemLog` | KEEP | Identical to reference, working |

### 1.4 command-center/ Sub-components

| File | What It Does | Store / Composable | Key Imports | Decision | Rationale |
|------|-------------|-------------------|-------------|----------|-----------|
| `command-center/EpicTaskTree.vue` | CLEO epic/task tree — expandable tree of epics and tasks from cleoStore | `useCleoStore` | `TaskDetailModal` | KEEP | tac-master-native (CLEO integration), working |
| `command-center/TaskDetailModal.vue` | Task detail modal — full CLEO task details (description, status, notes, acceptance criteria) | `useCleoStore` | — | KEEP | tac-master-native, working |

### 1.5 config/ Sub-components

| File | What It Does | Store / Composable | Key Imports | Decision | Rationale |
|------|-------------|-------------------|-------------|----------|-----------|
| `config/BudgetsConfigTab.vue` | Budget config tab — edit per-agent and global budget caps | `api` (from `api.ts`) | `types/BudgetsConfig` | KEEP | tac-master-native, working |
| `config/ModelPricesConfigTab.vue` | Model prices config tab — edit per-model cost rates | `api` | `types/ModelPricesConfig, ModelPrice` | KEEP | tac-master-native, working |
| `config/PoliciesConfigTab.vue` | Policies config tab — edit run policies (auto-approve, escalate, etc.) | `api` | `types/PoliciesConfig` | KEEP | tac-master-native, working |
| `config/ReposConfigTab.vue` | Repos config tab — add/remove/probe tracked repos | `api` | `types/ReposConfig, RepoEntry, RepoProbeResult` | KEEP | tac-master-native, working |

---

## 2. orchestrator_3_stream Components — Port / Skip

> Root: `orchestrator-agent-with-adws/apps/orchestrator_3_stream/frontend/src/`

| File | What It Does | Store / Composable | Key Decision | Rationale |
|------|-------------|-------------------|----|-----------|
| `App.vue` | 3-column layout: agents sidebar \| event stream \| chat panel | `useOrchestratorStore` | SKIP | tac-master has CommandCenterLayout with more complex slot-based layout |
| `components/AgentList.vue` | Reference agent list — base version without RoleBadge/inferRole | `useOrchestratorStore` | SKIP (already ported + enhanced) | tac-master AgentList.vue already extends the reference with T033 additions |
| `components/AppHeader.vue` | Stats header with ADWS view mode toggle | `useOrchestratorStore`, `useHeaderBar` | SKIP | tac-master has HeaderBar.vue (adapted). The orphaned AppHeader.vue copy in tac-master should be REMOVED |
| `components/EventStream.vue` | Center event log — reads `store.filteredEventStream` (pre-filtered by agent) | `useOrchestratorStore`, `useEventStreamFilter` | SKIP (already ported + fixed) | tac-master EventStream.vue already correct: reads full `eventStreamEntries` instead of `filteredEventStream` |
| `components/FilterControls.vue` | Filter bar — identical between repos | `useOrchestratorStore` | SKIP (already ported) | Identical in tac-master, working |
| `components/GlobalCommandInput.vue` | Cmd+K overlay with slash commands + autocomplete | `useOrchestratorStore`, `useAutocomplete`, `chatService` | SKIP | tac-master uses CommandBar.vue (T053) for operator input. GlobalCommandInput depends on orchestrator-specific slash commands and chatService/autocomplete not applicable to tac-master |
| `components/OrchestratorChat.vue` | Right sidebar chat UI — streaming chat, thinking/tool bubbles, chat width toggle, typing indicator | `useOrchestratorStore` (`chatMessages`, `isTyping`, `chatWidth`, `orchestratorAgent`, `toggleChatWidth`), `chat/ThinkingBubble`, `chat/ToolUseBubble` | **PORT + ADAPT** | Not yet in tac-master. Key missing feature for live orchestration chat. Must adapt: (1) remove `chatWidth`/`orchestratorAgent` store references not in tac-master store; (2) wire to tac-master `chatMessages` + `isTyping` state (to be added to orchestratorStore); (3) adapt send-message to POST `/api/chat` with graceful degrade |
| `components/AdwSwimlanes.vue` | ADW (Agent Decision Workflow) swimlane view — ADW-specific pipeline visualization | `useOrchestratorStore` (`adws`, `adwsLoading`, `fetchAdws`, `allAdwEventsByStep`) | SKIP | ADW is orchestrator_3_stream-specific. tac-master uses PITER pipeline (PipelineFlow.vue) instead |
| `components/chat/ThinkingBubble.vue` | Identical to tac-master version | None | SKIP (already ported) | Identical, already in tac-master |
| `components/chat/ToolUseBubble.vue` | Identical to tac-master version | None | SKIP (already ported) | Identical, already in tac-master |
| `components/event-rows/AgentLogRow.vue` | Base AgentLogRow — without T033 metadata enrichment | `useOrchestratorStore` (`fileTrackingEvents`) | SKIP (already ported + enhanced) | tac-master version adds phase/repo/adw_id metadata merging (T033) |
| `components/event-rows/AgentToolUseBlockRow.vue` | Identical to tac-master version | None | SKIP (already ported) | Identical |
| `components/event-rows/FileChangesDisplay.vue` | Identical to tac-master version | None | SKIP (already ported) | Identical |
| `components/event-rows/OrchestratorChatRow.vue` | Identical to tac-master version | None | SKIP (already ported) | Identical |
| `components/event-rows/SystemLogRow.vue` | Identical to tac-master version | None | SKIP (already ported) | Identical |
| `components/event-rows/ToolUseBlockRow.vue` | Orchestrator-specific tool use row for orchestrator's own tool calls (not agent tool calls) | None | SKIP | Not referenced by any component in orchestrator_3_stream EventStream. tac-master uses `AgentToolUseBlockRow` for all tool events |

---

## 3. Decision Summary Tables

### 3.1 tac-master: Decision Counts

| Decision | Count | Files |
|----------|-------|-------|
| KEEP | 33 | All working components |
| REMOVE | 1 | `AppHeader.vue` (orphan, identical to reference, not imported) |
| (Awaiting PORT) | 0 | OrchestratorChat not yet present in tac-master |

### 3.2 orchestrator_3_stream: Decision Counts

| Decision | Count | Files |
|----------|-------|-------|
| PORT + ADAPT | 1 | `components/OrchestratorChat.vue` |
| SKIP | 15 | All others (already ported, reference-specific, or superseded) |

---

## 4. Component Dependency Graph

```
App.vue
├── stores/orchestratorStore  (loadInitial, connectWebSocket)
├── CommandCenterLayout.vue
│   └── stores/orchestratorStore  (isConnected, repos)
│
├── [slot: header]
│   └── HeaderBar.vue
│       └── stores/orchestratorStore  (isConnected, repos)
│
├── [slot: sidebar]
│   ├── RepoSidebar.vue          (props: EpicSummary[], TaskSummary[])
│   ├── command-center/EpicTaskTree.vue
│   │   ├── stores/cleoStore
│   │   └── command-center/TaskDetailModal.vue
│   │       └── stores/cleoStore
│   └── OperatorLog.vue
│       └── stores/cleoStore
│
├── [slot: main — dashboard tab]
│   ├── ActiveAgentsPanel.vue
│   │   └── stores/orchestratorStore  (recentAgents, runningAgents, eventStreamEntries, selectAgent)
│   ├── PipelineFlow.vue
│   │   └── stores/orchestratorStore  (selectedRun, repos)
│   ├── IssueDetails.vue
│   │   └── stores/orchestratorStore  (selectedRun)
│   └── DependencyGraph.vue
│       └── stores/cleoStore  (tasks, epics)
│
├── [slot: right]
│   └── LiveExecutionPanel.vue
│       └── stores/orchestratorStore  (eventStreamEntries, selectedAgentId)
│
├── [slot: statusbar]
│   └── StatusBar.vue
│       └── stores/orchestratorStore  (stats, isConnected)
│
├── [tab: repos]
│   └── RepoBoard.vue
│       └── stores/orchestratorStore  (repos, agents)
│
├── [tab: config]
│   └── ConfigPage.vue
│       ├── api  (api.ts)
│       ├── config/ReposConfigTab.vue    → api
│       ├── config/BudgetsConfigTab.vue  → api
│       ├── config/PoliciesConfigTab.vue → api
│       └── config/ModelPricesConfigTab.vue → api
│
├── [tab: system-logs]
│   └── SystemLogs.vue  (standalone fetch, no store)
│
├── CommandBar.vue
│   └── stores/toastStore
│
├── Toast.vue
│   └── stores/toastStore
│
└── PhaseDetailModal.vue  (props/emits only)

EventStream.vue
├── stores/orchestratorStore  (eventStreamEntries — FULL stream)
├── composables/useEventStreamFilter  (multi-axis filter logic)
├── FilterControls.vue
│   ├── stores/orchestratorStore  (agents)
│   └── utils/agentColors
└── event-rows/
    ├── AgentLogRow.vue
    │   ├── stores/orchestratorStore  (fileTrackingEvents)
    │   ├── utils/agentColors
    │   ├── utils/markdown
    │   └── event-rows/FileChangesDisplay.vue
    │       └── services/fileService
    ├── AgentToolUseBlockRow.vue
    │   └── utils/agentColors
    ├── OrchestratorChatRow.vue
    │   └── utils/markdown
    └── SystemLogRow.vue

AgentList.vue
├── stores/orchestratorStore  (eventStreamEntries, isAgentPulsing)
├── utils/agentColors
├── utils/inferRole
└── RoleBadge.vue

RunDetailsPanel.vue
├── stores/orchestratorStore  (selectedRun, selectedAgent)
├── RoleBadge.vue
└── utils/inferRole

chat/ (standalone display components)
├── ThinkingBubble.vue  → utils/markdown
└── ToolUseBubble.vue   (no deps)

[FUTURE — T061-D]
OrchestratorChat.vue (to be ported from reference)
├── stores/orchestratorStore  (chatMessages, isTyping — NEW state)
├── chat/ThinkingBubble.vue
├── chat/ToolUseBubble.vue
└── utils/markdown
```

---

## 5. Key Findings

### 5.1 EventStream Filter Bug: Already Fixed

The spec (T061, section 1.3) identified that EventStream reads `store.filteredEventStream`
instead of `store.eventStreamEntries`. The tac-master EventStream.vue at line 100 already
reads `() => store.eventStreamEntries` (full stream). The category/level filter bug noted
in the spec is pre-resolved — no fix required for this item.

The reference orchestrator_3_stream EventStream still reads `store.filteredEventStream`
(the reference has not been fixed). This confirms the tac-master version is already ahead.

### 5.2 AppHeader.vue is a Dead Artifact

`AppHeader.vue` in tac-master is byte-for-byte identical to the reference
`orchestrator_3_stream/components/AppHeader.vue`. It is not imported anywhere in
tac-master. It should be deleted to reduce confusion and dead code. The correct tac-master
header is `HeaderBar.vue`.

### 5.3 OrchestratorChat.vue is the Only Port Needed

Of all orchestrator_3_stream components, only `OrchestratorChat.vue` needs porting.
All other reference components are either already in tac-master, superseded by
tac-master-native equivalents, or ADW/orchestrator-specific.

### 5.4 AgentList.vue vs ActiveAgentsPanel.vue

Both exist in tac-master with different purposes:
- `AgentList.vue`: Full rich card view (reference port + T033 enhancements). Currently
  appears unused in App.vue slots (not imported in App.vue). May be a candidate for
  integration as an alternative view, or removal if ActiveAgentsPanel fully replaces it.
- `ActiveAgentsPanel.vue`: tac-master-native simplified panel with stall detection and
  CLEO badge. Currently used in the main dashboard slot.

### 5.5 ToolUseBlockRow.vue (orchestrator_3_stream only)

`event-rows/ToolUseBlockRow.vue` exists in the reference but is not imported or used by
any component in orchestrator_3_stream (EventStream uses AgentToolUseBlockRow). It appears
to be legacy/dead code in the reference. Do not port.

---

## 6. Port Work Required (for T061 sub-tasks)

| Sub-task | Action | Files Affected |
|----------|--------|---------------|
| T061-A | EventStream filter already fixed — verify and close | `EventStream.vue` (no change needed) |
| T061-B | Services layer: `src/services/api.ts`, `src/services/chatService.ts` stub | 2 new files |
| T061-C | Add `chatMessages[]`, `isTyping`, `sendUserMessage()` to orchestratorStore | `stores/orchestratorStore.ts` |
| T061-D | Port `OrchestratorChat.vue` — adapt store refs for tac-master | 1 new file |
| T061-E | Integrate OrchestratorChat into App.vue layout | `App.vue`, `CommandCenterLayout.vue` |
| T061-F | Remove `AppHeader.vue` dead artifact | Delete 1 file |
| T061-G | Integration test — clean build verification | CI/build check |
