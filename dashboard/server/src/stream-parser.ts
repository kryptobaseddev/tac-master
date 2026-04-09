/**
 * stream-parser.ts — Parse raw_output.jsonl files from Claude Code agent runs.
 *
 * Each line in raw_output.jsonl is a JSON object from Claude's stream output.
 * The format is message-based: type="assistant" contains message.content[]
 * with blocks of type "thinking", "text", or "tool_use".
 *
 * type="user" contains tool_result responses (tool outputs).
 *
 * @task T039
 * @epic T036
 * @why Existing hook events only capture lifecycle transitions. Claude's actual
 *      thinking, responses, and tool calls live in raw_output.jsonl and must
 *      be parsed to show in the Live Execution panel.
 * @what Exports parseStreamFile() which reads a raw_output.jsonl path and
 *      returns an array of ParsedStreamEvent (thinking, response, tool_use)
 *      suitable for the /api/stream/:adw_id/:phase endpoint.
 */

import { createReadStream } from "node:fs";
import { createInterface } from "node:readline";
import { existsSync } from "node:fs";

// ─── Types ───────────────────────────────────────────────────────────────────

export type StreamEventType = "thinking" | "response" | "tool_use";

export interface ParsedStreamEvent {
  type: StreamEventType;
  /** ISO timestamp — from the user tool_result line that follows, or approximated */
  timestamp: string | null;
  /** For thinking: Claude's reasoning text */
  thinking?: string;
  /** For response: Claude's assistant text */
  text?: string;
  /** For tool_use: the tool name */
  tool_name?: string;
  /** For tool_use: human-readable description of the call */
  tool_summary?: string;
  /** For tool_use: raw input params (for clients that want them) */
  tool_input?: Record<string, unknown>;
  /** Message id from Claude (allows grouping) */
  message_id?: string;
}

// ─── Human-readable tool summary ─────────────────────────────────────────────

/**
 * Convert a tool_use content block into a human-readable one-liner.
 *
 * Examples:
 *   Bash { command: "git status", description: "Show git status" }
 *     → "Show git status"
 *   Read { file_path: "/srv/.../config.yaml" }
 *     → "Read /srv/.../config.yaml"
 *   Edit { file_path: "foo.ts", old_string: "...", new_string: "..." }
 *     → "Edit foo.ts (42 → 55 chars)"
 *   Write { file_path: "bar.ts", content: "..." }
 *     → "Write bar.ts"
 *   Grep { pattern: "ReflexContext", path: "./pkg" }
 *     → "Search for 'ReflexContext' in ./pkg"
 *   Glob { pattern: "**\/*.ts" }
 *     → "Glob **\/*.ts"
 */
export function toolSummary(toolName: string, input: Record<string, unknown>): string {
  const name = toolName.trim();

  switch (name) {
    case "Bash": {
      const desc = String(input.description ?? "").trim();
      const cmd = String(input.command ?? "").trim();
      if (desc) return desc;
      // truncate long commands
      return cmd.length > 120 ? cmd.slice(0, 120) + "…" : cmd;
    }

    case "Read": {
      const fp = String(input.file_path ?? "").trim();
      const offset = input.offset != null ? ` (L${input.offset})` : "";
      return `Read ${fp}${offset}`;
    }

    case "Edit": {
      const fp = String(input.file_path ?? "").trim();
      const oldLen = String(input.old_string ?? "").length;
      const newLen = String(input.new_string ?? "").length;
      const filename = fp.split("/").pop() ?? fp;
      return `Edit ${filename} (${oldLen} → ${newLen} chars)`;
    }

    case "Write": {
      const fp = String(input.file_path ?? "").trim();
      const contentLen = String(input.content ?? "").length;
      const filename = fp.split("/").pop() ?? fp;
      return `Write ${filename} (${contentLen} chars)`;
    }

    case "Glob": {
      const pattern = String(input.pattern ?? "").trim();
      const path = input.path ? ` in ${input.path}` : "";
      return `Glob ${pattern}${path}`;
    }

    case "Grep": {
      const pattern = String(input.pattern ?? "").trim();
      const path = input.path ? ` in ${input.path}` : "";
      return `Search '${pattern}'${path}`;
    }

    case "WebFetch": {
      const url = String(input.url ?? "").trim();
      const short = url.replace(/^https?:\/\//, "").slice(0, 60);
      return `Fetch ${short}`;
    }

    case "WebSearch": {
      const query = String(input.query ?? "").trim();
      return `Search "${query.slice(0, 80)}"`;
    }

    case "TodoWrite": {
      const todos = Array.isArray(input.todos) ? input.todos : [];
      return `Update TODO list (${todos.length} items)`;
    }

    default: {
      // Generic fallback: serialize first 100 chars of input
      const raw = JSON.stringify(input);
      return raw.length > 100 ? raw.slice(0, 100) + "…" : raw;
    }
  }
}

// ─── Core parser ─────────────────────────────────────────────────────────────

/**
 * Parse a raw_output.jsonl file and return structured stream events.
 *
 * The file format (per observation on LXC):
 *   type="system"    — init metadata, skip
 *   type="assistant" — message.content[] with thinking/text/tool_use blocks
 *   type="user"      — tool_result blocks; carry the timestamp of execution
 *
 * We read line-by-line, collect assistant blocks, and annotate each tool_use
 * with the timestamp from the immediately following user tool_result (if any).
 */
export async function parseStreamFile(filePath: string): Promise<ParsedStreamEvent[]> {
  if (!existsSync(filePath)) {
    return [];
  }

  const events: ParsedStreamEvent[] = [];

  // We track tool_use ids from the last assistant message so we can
  // attach the timestamp from the matching user tool_result.
  const pendingToolTimestamps = new Map<string, string>(); // tool_use_id → ISO

  const rl = createInterface({
    input: createReadStream(filePath, { encoding: "utf-8" }),
    crlfDelay: Infinity,
  });

  for await (const rawLine of rl) {
    const line = rawLine.trim();
    if (!line) continue;

    let obj: Record<string, unknown>;
    try {
      obj = JSON.parse(line);
    } catch {
      continue;
    }

    const lineType = String(obj.type ?? "");

    // ── Assistant messages contain thinking/text/tool_use blocks ──
    if (lineType === "assistant") {
      const msg = obj.message as Record<string, unknown> | undefined;
      if (!msg) continue;

      const content = Array.isArray(msg.content) ? msg.content : [];
      const msgId = String(msg.id ?? "");

      for (const block of content) {
        const b = block as Record<string, unknown>;
        const blockType = String(b.type ?? "");

        if (blockType === "thinking") {
          const thinkingText = String(b.thinking ?? "").trim();
          if (!thinkingText) continue;
          events.push({
            type: "thinking",
            timestamp: null, // will be set from next user message if available
            thinking: thinkingText,
            message_id: msgId,
          });
        } else if (blockType === "text") {
          const text = String(b.text ?? "").trim();
          if (!text) continue;
          events.push({
            type: "response",
            timestamp: null,
            text,
            message_id: msgId,
          });
        } else if (blockType === "tool_use") {
          const toolName = String(b.name ?? "");
          const toolId = String(b.id ?? "");
          const input = (b.input as Record<string, unknown>) ?? {};
          const summary = toolSummary(toolName, input);

          const ev: ParsedStreamEvent = {
            type: "tool_use",
            timestamp: null,
            tool_name: toolName,
            tool_summary: summary,
            tool_input: input,
            message_id: msgId,
          };
          events.push(ev);

          // Track this tool_use id so we can stamp it when the user result arrives
          if (toolId) {
            pendingToolTimestamps.set(toolId, "__pending__");
          }
        }
      }
    }

    // ── User messages carry tool_result content with timestamps ──
    if (lineType === "user") {
      const ts = String(obj.timestamp ?? "").trim();
      const msg = obj.message as Record<string, unknown> | undefined;
      if (!msg) continue;

      const content = Array.isArray(msg.content) ? msg.content : [];
      for (const block of content) {
        const b = block as Record<string, unknown>;
        if (String(b.type ?? "") === "tool_result") {
          const toolUseId = String(b.tool_use_id ?? "");
          // Find the most recent tool_use event that matches this id
          if (ts && toolUseId) {
            // Walk backwards to find the tool_use without a timestamp
            for (let i = events.length - 1; i >= 0; i--) {
              const ev = events[i];
              if (ev.type === "tool_use" && !ev.timestamp) {
                ev.timestamp = ts;
                break;
              }
            }
          }

          // Also stamp any recent thinking/response events without a timestamp
          // using the first tool_result timestamp we see as a rough approximation
          if (ts) {
            for (let i = events.length - 1; i >= 0; i--) {
              const ev = events[i];
              if (ev.timestamp) break; // already stamped, stop
              ev.timestamp = ts;
            }
          }
        }
      }
    }
  }

  return events;
}

// ─── File path helpers ────────────────────────────────────────────────────────

const REPOS_BASE = process.env.REPOS_BASE ?? "/srv/tac-master/repos";

/**
 * Resolve the raw_output.jsonl path for a given adw_id + phase.
 *
 * Layout on LXC:
 *   /srv/tac-master/repos/<repo_slug>/agents/<adw_id>/<phase>/raw_output.jsonl
 *
 * We glob across all repo slugs since we may not know which one owns the adw_id.
 */
export function resolveStreamPath(adwId: string, phase: string): string | null {
  // Fast path: iterate known repo dirs
  let reposDir: string[];
  try {
    const { readdirSync } = require("node:fs");
    reposDir = readdirSync(REPOS_BASE) as string[];
  } catch {
    return null;
  }

  for (const repoSlug of reposDir) {
    const candidate = `${REPOS_BASE}/${repoSlug}/agents/${adwId}/${phase}/raw_output.jsonl`;
    if (existsSync(candidate)) {
      return candidate;
    }
  }
  return null;
}

/**
 * List all phases that have a raw_output.jsonl for a given adw_id.
 */
export function listStreamPhases(adwId: string): Array<{ phase: string; path: string }> {
  let reposDir: string[];
  try {
    const { readdirSync } = require("node:fs");
    reposDir = readdirSync(REPOS_BASE) as string[];
  } catch {
    return [];
  }

  const results: Array<{ phase: string; path: string }> = [];

  for (const repoSlug of reposDir) {
    const agentsDir = `${REPOS_BASE}/${repoSlug}/agents/${adwId}`;
    let phases: string[];
    try {
      const { readdirSync } = require("node:fs");
      phases = readdirSync(agentsDir) as string[];
    } catch {
      continue;
    }

    for (const phase of phases) {
      const candidate = `${agentsDir}/${phase}/raw_output.jsonl`;
      if (existsSync(candidate)) {
        results.push({ phase, path: candidate });
      }
    }
  }

  return results;
}
