# T037: Command Center Layout Shell — Implementation Output

**Task**: T037 | **Epic**: T036 | **Branch**: T037-layout-shell  
**Status**: complete  
**Date**: 2026-04-09

## Files Created

### Client — New Vue Components

- `dashboard/client/src/components/CommandCenterLayout.vue`  
  Master CSS Grid shell with `header`, `sidebar` (within body flex), `main`, and `statusbar` areas. Exports named slots for each area. Collapses sidebar on mobile < 768px.

- `dashboard/client/src/components/HeaderBar.vue`  
  COMMAND_CENTER branding left with green/red WebSocket dot. Tab nav (Dashboard/Repos/Config) center. Repo selector dropdown right using `store.repos`. Notification and Settings icon buttons. Emits `update:activeTab` and `update:currentRepoUrl`.

- `dashboard/client/src/components/RepoSidebar.vue`  
  Repo list from `store.repos`. Status dot logic: green=active_runs>0, red=failed_today>0, gray=idle. Failed count badge (red) and active count badge (green) per row. Click emits `select-repo`. Bottom nav: ACTIVE_PIPELINES, SYSTEM_LOGS, DOCS.

- `dashboard/client/src/components/StatusBar.vue`  
  KPI bar: LIVE_RUNS (green pulsing dot), REPOS, RUNS, TOKENS/DAY, COST/DAY. Primary source: Pinia store (real-time via WS). Fallback: polls `/api/stats` every 10s.

### Client — Styles

- `dashboard/client/src/styles/command-center.css`  
  CSS custom properties: `--cc-bg: #0a0a0a`, `--cc-surface: #111`, `--cc-border: #1a1a1a`, `--cc-cyan: #00ffcc`, `--cc-green: #00ff66`, `--cc-red: #ff4466`, `--cc-text: #e0e0e0`, `--cc-text-muted: #666`. Utility classes: `.cc-dot-green/red/gray`, `.cc-pulse`, `.cc-glow-cyan/green`, `.cc-badge-*`.

- `dashboard/client/src/main.ts` — added `import "./styles/command-center.css"` import.

### Server — New Endpoint

- `dashboard/server/src/db.ts` — added `getAggregateStats()` function:
  - Queries: `live_runs` (runs WHERE status IN pending/running), `total_repos`, `total_runs`, `tokens_today` (token_ledger SUM), `cost_today_usd` (token_ledger SUM).
  - Graceful fallback to `budget_usage` if `token_ledger` table missing.

- `dashboard/server/src/index.ts` — added `GET /api/stats` route returning:
  ```json
  {"live_runs":0,"total_repos":4,"tokens_today":40415,"cost_today_usd":12.48,"total_runs":18}
  ```

## Deployment

- Built on LXC 114 (10.0.10.22): client rebuilt `npm run build` — clean 275 modules.
- Server restarted: `systemctl restart tac-master-dashboard`.
- `/api/stats` endpoint verified live: returns real data from production DB.

## Acceptance Criteria Status

1. App-level grid layout component with header/sidebar/main/status-bar areas — DONE (CommandCenterLayout.vue)
2. Header with branding, nav, repo selector — DONE (HeaderBar.vue)
3. Sidebar with repo list from API + status indicators — DONE (RepoSidebar.vue)
4. Status bar with LIVE_RUNS, REPOS, TOKENS/DAY, COST/DAY — DONE (StatusBar.vue)
5. Dark theme matching mockup — DONE (command-center.css with cyan/green accents)
6. Existing pages still render (via slots/router-view ready) — DONE (slot-based composition)

## Integration Note

These components are NEW files only. `App.vue` and existing components were NOT modified. Integration is a separate task. Components use `useOrchestratorStore()` directly for data.
