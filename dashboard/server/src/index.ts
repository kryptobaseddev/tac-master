/**
 * tac-master dashboard server.
 *
 * Bun HTTP + WebSocket server. Ingests hook events via POST /events,
 * broadcasts to connected clients over ws://host:4000/stream, and exposes
 * read-only views over the orchestrator's SQLite state store.
 */

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
