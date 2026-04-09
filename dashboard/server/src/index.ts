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
} from "./db";
import type { HookEvent, WsMessage } from "./types";

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

    // --- Lessons feed (knowledge base) ---
    if (url.pathname === "/api/lessons" && req.method === "GET") {
      const limit = Number(url.searchParams.get("limit") ?? 20);
      return json({ lessons: getLessons(limit) });
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
