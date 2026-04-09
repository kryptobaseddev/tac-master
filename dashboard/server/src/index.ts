/**
 * tac-master dashboard server.
 *
 * Bun HTTP + WebSocket server. Ingests hook events via POST /events,
 * broadcasts to connected clients over ws://host:4000/stream, exposes
 * read-only views over the orchestrator's SQLite state store, and serves
 * the built Vue client as static assets at /.
 *
 * Access the dashboard at http://<host>:4000/ once the client is built.
 */

import { join, resolve } from "node:path";
import { existsSync, statSync } from "node:fs";
import {
  initDatabase,
  insertEvent,
  getRecentEvents,
  getFilterOptions,
  getRepoStatuses,
  getActiveAndRecentRuns,
  getLessons,
  getRunPhases,
  getAggregateStats,
} from "./db";
import {
  getReposConfig,
  saveReposConfig,
  addRepoEntry,
  updateRepoEntry,
  deleteRepoEntry,
  getBudgetsConfig,
  saveBudgetsConfig,
  getPoliciesConfig,
  savePoliciesConfig,
  getModelPricesConfig,
  saveModelPricesConfig,
  probeGitHubRepo,
  restartDaemon,
  retryIssue,
  type RepoEntry,
} from "./config";
import type { HookEvent, WsMessage } from "./types";
import { getEpics, getTasksByParent, getTaskById, getCleoStats } from "./cleo-api";
import { readFile } from "node:fs/promises";
import { parseStreamFile, resolveStreamPath, listStreamPhases } from "./stream-parser";

const PORT = Number(process.env.PORT ?? 4000);
const ALLOW_ORIGIN = process.env.CORS_ORIGIN ?? "*";

// Locate the client dist/ directory. First try the sibling client/dist
// (for dev runs), then the container layout, then bail and serve a
// "client not built" fallback.
const CLIENT_DIST_CANDIDATES = [
  resolve(import.meta.dir, "..", "..", "client", "dist"),
  resolve(import.meta.dir, "..", "..", "..", "dashboard", "client", "dist"),
  "/srv/tac-master/dashboard/client/dist",
];
const CLIENT_DIST =
  CLIENT_DIST_CANDIDATES.find((p) => existsSync(p) && statSync(p).isDirectory()) ?? null;

if (CLIENT_DIST) {
  console.log(`[tac-master dashboard] serving client from ${CLIENT_DIST}`);
} else {
  console.log(
    `[tac-master dashboard] client not built — '/' will serve a fallback page.\n` +
      `  Build with: cd dashboard/client && npm install --legacy-peer-deps && npm run build`,
  );
}

const FALLBACK_HTML = `<!doctype html>
<html>
  <head><meta charset="utf-8"><title>tac-master</title>
    <style>
      body { font-family: ui-monospace, Menlo, Consolas, monospace;
             background: #12171e; color: #e4e7eb; padding: 3rem;
             max-width: 720px; margin: 0 auto; line-height: 1.55; }
      a { color: #3b82f6; }
      h1 { color: #a855f7; }
      code { background: #1f2933; padding: 0.15rem 0.4rem; border-radius: 3px; }
      .ok { color: #10b981; }
      .warn { color: #f59e0b; }
    </style>
  </head>
  <body>
    <h1>tac-master dashboard</h1>
    <p class="ok">✓ API server running on this port.</p>
    <p class="warn">⚠ Client bundle not found. Build it with:</p>
    <pre><code>pct enter &lt;CTID&gt;
cd /srv/tac-master/dashboard/client
sudo -u krypto npm install --legacy-peer-deps
sudo -u krypto npm run build
systemctl restart tac-master-dashboard</code></pre>
    <h2>API endpoints</h2>
    <ul>
      <li><a href="/health">GET /health</a></li>
      <li><a href="/api/repos">GET /api/repos</a></li>
      <li><a href="/api/runs">GET /api/runs</a></li>
      <li><a href="/api/lessons">GET /api/lessons</a></li>
      <li><a href="/events/recent">GET /events/recent</a></li>
      <li><a href="/events/filter-options">GET /events/filter-options</a></li>
      <li>WebSocket: <code>ws://&lt;host&gt;:4000/stream</code></li>
    </ul>
  </body>
</html>`;

initDatabase();

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": ALLOW_ORIGIN,
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...CORS_HEADERS },
  });
}

const clients = new Set<any>();

function broadcast(msg: WsMessage): void {
  const payload = JSON.stringify(msg);
  for (const ws of clients) {
    try {
      ws.send(payload);
    } catch {
      clients.delete(ws);
    }
  }
}

// Broadcast run/repo status updates every 5s so the UI is self-refreshing
// even without hook events firing.
setInterval(() => {
  try {
    const repos = getRepoStatuses();
    const runs = getActiveAndRecentRuns(30);
    for (const r of repos) broadcast({ type: "repo_status", data: r });
    for (const run of runs) broadcast({ type: "run_update", data: run });
  } catch (e) {
    console.error("[poll] error:", e);
  }
}, 5000);

const server = Bun.serve({
  port: PORT,
  development: false,

  async fetch(req, server) {
    const url = new URL(req.url);

    // --- CORS preflight ---
    if (req.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS_HEADERS });
    }

    // --- WebSocket upgrade ---
    if (url.pathname === "/stream") {
      if (server.upgrade(req)) return;
      return new Response("WebSocket upgrade failed", { status: 400 });
    }

    // --- Health ---
    if (url.pathname === "/health") {
      return json({
        status: "ok",
        service: "tac-master-dashboard",
        clients: clients.size,
      });
    }

    // --- Hook ingestion ---
    if (url.pathname === "/events" && req.method === "POST") {
      try {
        const body = (await req.json()) as HookEvent;
        if (!body.source_app || !body.session_id || !body.hook_event_type) {
          return json({ error: "missing required fields" }, 400);
        }
        const saved = insertEvent(body);
        broadcast({ type: "event", data: saved });
        return json(saved, 201);
      } catch (e: any) {
        return json({ error: String(e?.message ?? e) }, 400);
      }
    }

    // --- Recent events (hydrate on first load) ---
    if (url.pathname === "/events/recent" && req.method === "GET") {
      const limit = Number(url.searchParams.get("limit") ?? 100);
      const repoUrl = url.searchParams.get("repo_url") ?? undefined;
      return json(getRecentEvents(limit, repoUrl));
    }

    // --- Filter dropdown data ---
    if (url.pathname === "/events/filter-options" && req.method === "GET") {
      return json(getFilterOptions());
    }

    // --- Per-repo status board ---
    if (url.pathname === "/api/repos" && req.method === "GET") {
      return json({ repos: getRepoStatuses() });
    }

    // --- Active + recent runs ---
    if (url.pathname === "/api/runs" && req.method === "GET") {
      const limit = Number(url.searchParams.get("limit") ?? 50);
      return json({ runs: getActiveAndRecentRuns(limit) });
    }

    // --- Phase breakdown for a specific run (T038) ---
    // GET /api/runs/:adw_id/phases
    // Returns PITER phase list with status per phase derived from events + run state.
    const phaseMatch = url.pathname.match(/^\/api\/runs\/([^/]+)\/phases$/);
    if (phaseMatch && req.method === "GET") {
      const adwId = decodeURIComponent(phaseMatch[1]);
      return json({ adw_id: adwId, phases: getRunPhases(adwId) });
    }

    // --- Lessons feed (knowledge base) ---
    if (url.pathname === "/api/lessons" && req.method === "GET") {
      const limit = Number(url.searchParams.get("limit") ?? 20);
      return json({ lessons: getLessons(limit) });
    }

    // --- Aggregate KPI stats (T037 Command Center status bar) ---
    if (url.pathname === "/api/stats" && req.method === "GET") {
      return json(getAggregateStats());
    }

    // ============================================================
    // Config CRUD — YAML read/write with daemon restart support
    // ============================================================

    // GET /api/config/repos
    if (url.pathname === "/api/config/repos" && req.method === "GET") {
      try {
        return json(getReposConfig());
      } catch (e: any) {
        return json({ error: String(e?.message ?? e) }, 500);
      }
    }

    // PUT /api/config/repos — replace entire repos.yaml (full-object save)
    if (url.pathname === "/api/config/repos" && req.method === "PUT") {
      try {
        const body = (await req.json()) as any;
        saveReposConfig(body);
        return json({ ok: true, repos: body.repos?.length ?? 0 });
      } catch (e: any) {
        return json({ error: String(e?.message ?? e) }, 400);
      }
    }

    // POST /api/config/repos — add a single new repo entry
    if (url.pathname === "/api/config/repos" && req.method === "POST") {
      try {
        const body = (await req.json()) as RepoEntry;
        if (!body.url) return json({ error: "url is required" }, 400);
        const updated = addRepoEntry(body);
        return json({ ok: true, repos: updated.repos }, 201);
      } catch (e: any) {
        return json({ error: String(e?.message ?? e) }, 400);
      }
    }

    // PATCH /api/config/repos?url=... — update a single repo's fields
    if (url.pathname === "/api/config/repos" && req.method === "PATCH") {
      try {
        const target = url.searchParams.get("url");
        if (!target) return json({ error: "url query param required" }, 400);
        const body = (await req.json()) as Partial<RepoEntry>;
        const updated = updateRepoEntry(target, body);
        return json({ ok: true, repos: updated.repos });
      } catch (e: any) {
        return json({ error: String(e?.message ?? e) }, 400);
      }
    }

    // DELETE /api/config/repos?url=... — remove a repo
    if (url.pathname === "/api/config/repos" && req.method === "DELETE") {
      try {
        const target = url.searchParams.get("url");
        if (!target) return json({ error: "url query param required" }, 400);
        const updated = deleteRepoEntry(target);
        return json({ ok: true, repos: updated.repos });
      } catch (e: any) {
        return json({ error: String(e?.message ?? e) }, 400);
      }
    }

    // GET /api/config/budgets
    if (url.pathname === "/api/config/budgets" && req.method === "GET") {
      try {
        return json(getBudgetsConfig());
      } catch (e: any) {
        return json({ error: String(e?.message ?? e) }, 500);
      }
    }

    // PUT /api/config/budgets — full-object save
    if (url.pathname === "/api/config/budgets" && req.method === "PUT") {
      try {
        const body = (await req.json()) as any;
        saveBudgetsConfig(body);
        return json({ ok: true });
      } catch (e: any) {
        return json({ error: String(e?.message ?? e) }, 400);
      }
    }

    // GET /api/config/policies
    if (url.pathname === "/api/config/policies" && req.method === "GET") {
      try {
        return json(getPoliciesConfig());
      } catch (e: any) {
        return json({ error: String(e?.message ?? e) }, 500);
      }
    }

    // PUT /api/config/policies
    if (url.pathname === "/api/config/policies" && req.method === "PUT") {
      try {
        const body = (await req.json()) as any;
        savePoliciesConfig(body);
        return json({ ok: true });
      } catch (e: any) {
        return json({ error: String(e?.message ?? e) }, 400);
      }
    }

    // GET /api/config/model-prices
    if (url.pathname === "/api/config/model-prices" && req.method === "GET") {
      try {
        return json(getModelPricesConfig());
      } catch (e: any) {
        return json({ error: String(e?.message ?? e) }, 500);
      }
    }

    // PUT /api/config/model-prices
    if (url.pathname === "/api/config/model-prices" && req.method === "PUT") {
      try {
        const body = (await req.json()) as any;
        saveModelPricesConfig(body);
        return json({ ok: true });
      } catch (e: any) {
        return json({ error: String(e?.message ?? e) }, 400);
      }
    }

    // GET /api/github/repo-info?url=...
    if (url.pathname === "/api/github/repo-info" && req.method === "GET") {
      const target = url.searchParams.get("url");
      if (!target) return json({ error: "url query param required" }, 400);
      try {
        const result = await probeGitHubRepo(target);
        return json(result);
      } catch (e: any) {
        return json({ error: String(e?.message ?? e) }, 400);
      }
    }

    // POST /api/system/restart-daemon — reloads the tac-master daemon
    // (applies config changes). Requires NOPASSWD sudoers rule for
    // `sudo -n systemctl restart tac-master`.
    if (url.pathname === "/api/system/restart-daemon" && req.method === "POST") {
      try {
        const body = req.body
          ? ((await req.json().catch(() => ({}))) as { service?: string })
          : {};
        const service = body.service ?? "tac-master";
        const result = await restartDaemon(service);
        return json(result, result.ok ? 200 : 500);
      } catch (e: any) {
        return json({ error: String(e?.message ?? e) }, 500);
      }
    }

    /**
     * POST /api/ops/retry-issue
     *
     * Reset a failed/aborted issue so the next poll cycle re-dispatches it.
     * Body: { issue_number: number, repo_url: string }
     *
     * Delegates to orchestrator/ops.py via `uv run` so the guard logic lives
     * in exactly one place (Python), regardless of whether the retry comes
     * from the dashboard or the CLI.
     *
     * @task T012
     * @epic T004
     * @why Provides the dashboard's Retry button a backend endpoint that calls
     *      the same service method as the CLI, ensuring consistent status guards.
     * @what Spawns `uv run orchestrator/ops.py retry <issue> <repo>` and
     *      returns its exit code + stdout/stderr as JSON.
     */
    if (url.pathname === "/api/ops/retry-issue" && req.method === "POST") {
      try {
        const body = (await req.json()) as { issue_number?: number; repo_url?: string };
        if (!body.issue_number || !body.repo_url) {
          return json({ ok: false, error: "issue_number and repo_url are required" }, 400);
        }
        const result = await retryIssue(body.issue_number, body.repo_url);
        return json(result, result.ok ? 200 : 422);
      } catch (e: any) {
        return json({ ok: false, error: String(e?.message ?? e) }, 500);
      }
    }

    // ============================================================
    // Stream parser endpoints (T039)
    // ============================================================

    /**
     * GET /api/stream/:adw_id/:phase
     *
     * Parse raw_output.jsonl for a specific agent run phase and return
     * structured thinking / response / tool_use events.
     *
     * Query params:
     *   limit (default 200) — cap number of events returned
     *
     * @task T039
     * @epic T036
     */
    const streamMatch = url.pathname.match(/^\/api\/stream\/([^/]+)\/([^/]+)$/);
    if (streamMatch && req.method === "GET") {
      const adwId = decodeURIComponent(streamMatch[1]);
      const phase = decodeURIComponent(streamMatch[2]);
      const limit = Number(url.searchParams.get("limit") ?? 200);

      const filePath = resolveStreamPath(adwId, phase);
      if (!filePath) {
        return json({ error: "raw_output.jsonl not found for this adw_id/phase" }, 404);
      }

      try {
        const events = await parseStreamFile(filePath);
        const sliced = events.slice(0, limit);
        return json({ adw_id: adwId, phase, events: sliced, total: events.length });
      } catch (e: any) {
        return json({ error: String(e?.message ?? e) }, 500);
      }
    }

    /**
     * GET /api/stream/:adw_id
     *
     * List all phases that have a raw_output.jsonl for this adw_id.
     *
     * @task T039
     */
    const streamRootMatch = url.pathname.match(/^\/api\/stream\/([^/]+)$/);
    if (streamRootMatch && req.method === "GET") {
      const adwId = decodeURIComponent(streamRootMatch[1]);
      const phases = listStreamPhases(adwId);
      return json({ adw_id: adwId, phases: phases.map((p) => p.phase) });
    }

    // ============================================================
    // CLEO task-tree API  (T040)
    // ============================================================

    // GET /api/cleo/epics — all epics with child-task progress
    if (url.pathname === "/api/cleo/epics" && req.method === "GET") {
      try {
        return json(getEpics());
      } catch (e: any) {
        return json({ epics: [], error: String(e?.message ?? e) }, 500);
      }
    }

    // GET /api/cleo/tasks?parent=TXXX — direct children of an epic/task
    if (url.pathname === "/api/cleo/tasks" && req.method === "GET") {
      const parentId = url.searchParams.get("parent");
      if (!parentId) return json({ error: "parent query param required" }, 400);
      try {
        return json({ tasks: getTasksByParent(parentId) });
      } catch (e: any) {
        return json({ tasks: [], error: String(e?.message ?? e) }, 500);
      }
    }

    // GET /api/cleo/task/:id — single task detail
    if (url.pathname.startsWith("/api/cleo/task/") && req.method === "GET") {
      const id = url.pathname.replace("/api/cleo/task/", "").split("/")[0];
      if (!id) return json({ error: "task id required" }, 400);
      try {
        const task = getTaskById(id);
        if (!task) return json({ error: "not found" }, 404);
        return json(task);
      } catch (e: any) {
        return json({ error: String(e?.message ?? e) }, 500);
      }
    }

    // GET /api/cleo/stats — aggregate task counts across all epics (T043)
    if (url.pathname === "/api/cleo/stats" && req.method === "GET") {
      try {
        return json(getCleoStats());
      } catch (e: any) {
        return json({ total: 0, done: 0, active: 0, pending: 0, blocked: 0, error: String(e?.message ?? e) }, 500);
      }
    }

    // GET /api/logs/daemon — last 100 lines of tac-master daemon log (T043)
    if (url.pathname === "/api/logs/daemon" && req.method === "GET") {
      const limit = Number(url.searchParams.get("limit") ?? 100);

      // Try journalctl first (available on systemd LXC), then fall back to file
      try {
        const journalProc = Bun.spawnSync(
          ["journalctl", "-u", "tac-master", "-n", String(limit), "--no-pager", "--output=short-iso"],
          { timeout: 5000 },
        );
        if (journalProc.exitCode === 0) {
          const stdout = new TextDecoder().decode(journalProc.stdout);
          if (stdout.trim()) {
            const lines = stdout.trim().split("\n").filter(Boolean);
            return json({ lines, source: "journalctl -u tac-master" });
          }
        }
      } catch {
        // journalctl not available — fall through to file
      }

      // Fallback: read daemon.stdout.log file
      const LOG_CANDIDATES = [
        "/srv/tac-master/logs/daemon.stdout.log",
        "/var/log/tac-master/daemon.stdout.log",
      ];

      for (const logPath of LOG_CANDIDATES) {
        try {
          const content = await readFile(logPath, "utf-8");
          const allLines = content.split("\n");
          const lines = allLines.slice(-limit).filter(Boolean);
          return json({ lines, source: logPath });
        } catch {
          // try next candidate
        }
      }

      return json({
        lines: [],
        source: "",
        error: "No log source available (journalctl failed and no log file found)",
      });
    }

    // --- Static client (Vue SPA) ---
    if (req.method === "GET") {
      if (CLIENT_DIST) {
        // Default to index.html for /, otherwise try the requested path
        const relPath = url.pathname === "/" ? "/index.html" : url.pathname;
        const filePath = join(CLIENT_DIST, relPath);
        const file = Bun.file(filePath);
        if (await file.exists()) {
          return new Response(file);
        }
        // SPA fallback: unknown routes serve index.html so client-side routing works
        const indexFile = Bun.file(join(CLIENT_DIST, "index.html"));
        if (await indexFile.exists()) {
          return new Response(indexFile, {
            headers: { "Content-Type": "text/html" },
          });
        }
      }
      if (url.pathname === "/") {
        return new Response(FALLBACK_HTML, {
          headers: { "Content-Type": "text/html", ...CORS_HEADERS },
        });
      }
    }

    return new Response("Not found", { status: 404, headers: CORS_HEADERS });
  },

  websocket: {
    open(ws) {
      clients.add(ws);
      // Hydrate with recent events + current runs + repo statuses
      ws.send(
        JSON.stringify({
          type: "initial",
          data: getRecentEvents(50),
        } satisfies WsMessage),
      );
      for (const r of getRepoStatuses()) {
        ws.send(JSON.stringify({ type: "repo_status", data: r } satisfies WsMessage));
      }
      for (const run of getActiveAndRecentRuns(30)) {
        ws.send(JSON.stringify({ type: "run_update", data: run } satisfies WsMessage));
      }
    },
    close(ws) {
      clients.delete(ws);
    },
    message(_ws, _msg) {
      // reserved for future client → server commands (pause, filter sync)
    },
  },
});

console.log(
  `[tac-master dashboard] listening on http://localhost:${server.port} (ws://localhost:${server.port}/stream)`,
);
