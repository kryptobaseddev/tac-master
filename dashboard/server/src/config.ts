/**
 * YAML config file management for the dashboard.
 *
 * Reads and writes the four config files in tac-master/config/:
 *   repos.yaml          — allowlisted repositories
 *   budgets.yaml        — cost controls
 *   policies.yaml       — execution policies
 *   model_prices.yaml   — per-model token pricing
 *
 * Also probes GitHub to determine repo visibility when adding a new entry.
 *
 * Changes to these files do NOT auto-apply to the running daemon. The
 * caller must invoke restartDaemon() to pick up changes.
 */

import yaml from "js-yaml";
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { join, resolve } from "node:path";

const TAC_HOME = process.env.TAC_MASTER_HOME ?? "/srv/tac-master";
const CONFIG_DIR = join(TAC_HOME, "config");

const PATHS = {
  repos: join(CONFIG_DIR, "repos.yaml"),
  budgets: join(CONFIG_DIR, "budgets.yaml"),
  policies: join(CONFIG_DIR, "policies.yaml"),
  modelPrices: join(CONFIG_DIR, "model_prices.yaml"),
} as const;

// ---------------------------------------------------------------------------
// Generic YAML helpers
// ---------------------------------------------------------------------------

function readYaml<T = unknown>(path: string): T {
  if (!existsSync(path)) {
    throw new Error(`Config file missing: ${path}`);
  }
  const raw = readFileSync(path, "utf8");
  return yaml.load(raw) as T;
}

function writeYaml(path: string, data: unknown): void {
  // Preserve a stable key order with `sortKeys: false` and write with
  // 2-space indent + line width of 120 so humans can still edit.
  const text = yaml.dump(data, {
    indent: 2,
    lineWidth: 120,
    sortKeys: false,
    noRefs: true,
  });
  writeFileSync(path, text, "utf8");
}

// ---------------------------------------------------------------------------
// Typed shapes (match orchestrator/config.py)
// ---------------------------------------------------------------------------

export interface RepoEntry {
  url: string;
  self?: boolean;
  default_workflow?: string;
  model_set?: string;
  poll_interval?: number;
  triggers?: string[];
  trigger_labels?: string[];
  auto_merge?: boolean;
  dry_run?: boolean;
  runtime?: string;
  container_image?: string;
  env?: Record<string, string>;
}

export interface ReposConfig {
  version: number;
  defaults: Record<string, unknown>;
  repos: RepoEntry[];
}

export interface BudgetsConfig {
  version: number;
  global?: {
    max_tokens_per_day?: number;
    max_runs_per_day?: number;
    max_concurrent_runs?: number;
    on_exceeded?: string;
  };
  defaults?: {
    max_tokens_per_day?: number;
    max_runs_per_day?: number;
    max_concurrent_runs?: number;
    max_tokens_per_run?: number;
  };
  repos?: Array<{
    url: string;
    max_tokens_per_day?: number;
    max_runs_per_day?: number;
    max_concurrent_runs?: number;
    max_tokens_per_run?: number;
  }>;
  alerts?: {
    warn_at_pct?: number;
    halt_notify_issue?: boolean;
  };
}

export interface PoliciesConfig {
  version: number;
  safety?: {
    protected_branches?: string[];
    forbidden_paths?: string[];
    max_files_per_pr?: number;
    max_diff_lines_per_pr?: number;
    require_tests_pass?: boolean;
  };
  workflows?: Record<string, {
    requires?: string[];
    max_per_repo_per_day?: number;
    max_files_override?: number;
    max_diff_lines_override?: number;
  }>;
  self_improvement?: {
    allow_auto_merge?: boolean;
    require_tests_pass?: boolean;
    post_merge_health_check?: {
      enabled?: boolean;
      timeout_minutes?: number;
      revert_on_failure?: boolean;
    };
  };
}

export interface ModelPrice {
  input: number;
  output: number;
  cache_write: number;
  cache_read: number;
}

export interface ModelPricesConfig {
  version: number;
  prices: Record<string, ModelPrice>;
}

// ---------------------------------------------------------------------------
// Validation + normalization helpers
// ---------------------------------------------------------------------------

export function normalizeRepoUrl(url: string): string {
  let u = url.trim();
  // Strip trailing .git and trailing slash
  u = u.replace(/\.git$/, "").replace(/\/$/, "");
  // Handle git@github.com:owner/repo → https://github.com/owner/repo
  if (u.startsWith("git@")) {
    const [, host, path] = u.match(/^git@([^:]+):(.+)$/) ?? [];
    if (host && path) u = `https://${host}/${path}`;
  }
  // Ensure https:// prefix
  if (!u.startsWith("http://") && !u.startsWith("https://")) {
    u = `https://${u}`;
  }
  return u;
}

export function extractOwnerRepo(url: string): { owner: string; repo: string } {
  const normalized = normalizeRepoUrl(url);
  const m = normalized.match(/github\.com\/([^/]+)\/([^/]+)/);
  if (!m) throw new Error(`Not a GitHub URL: ${url}`);
  return { owner: m[1], repo: m[2] };
}

// ---------------------------------------------------------------------------
// Repos config CRUD
// ---------------------------------------------------------------------------

export function getReposConfig(): ReposConfig {
  return readYaml<ReposConfig>(PATHS.repos);
}

export function saveReposConfig(cfg: ReposConfig): void {
  if (!cfg.version) cfg.version = 1;
  if (!Array.isArray(cfg.repos)) cfg.repos = [];
  writeYaml(PATHS.repos, cfg);
}

export function addRepoEntry(entry: RepoEntry): ReposConfig {
  const cfg = getReposConfig();
  entry.url = normalizeRepoUrl(entry.url);

  // Reject duplicates
  if (cfg.repos.find((r) => normalizeRepoUrl(r.url) === entry.url)) {
    throw new Error(`Repo already exists: ${entry.url}`);
  }

  // Fill in defaults
  const withDefaults: RepoEntry = {
    url: entry.url,
    self: entry.self ?? false,
    default_workflow: entry.default_workflow ?? "sdlc",
    model_set: entry.model_set ?? "base",
    auto_merge: entry.auto_merge ?? false,
    runtime: entry.runtime ?? "native",
    container_image: entry.container_image ?? "tac-worker:latest",
    triggers: entry.triggers ?? ["new_issue", "comment_adw", "label"],
    trigger_labels: entry.trigger_labels ?? ["tac-master", "krypto"],
  };

  cfg.repos.push(withDefaults);
  saveReposConfig(cfg);
  return cfg;
}

export function updateRepoEntry(url: string, patch: Partial<RepoEntry>): ReposConfig {
  const cfg = getReposConfig();
  const target = normalizeRepoUrl(url);
  const idx = cfg.repos.findIndex((r) => normalizeRepoUrl(r.url) === target);
  if (idx === -1) throw new Error(`Repo not found: ${url}`);
  cfg.repos[idx] = { ...cfg.repos[idx], ...patch, url: target };
  saveReposConfig(cfg);
  return cfg;
}

export function deleteRepoEntry(url: string): ReposConfig {
  const cfg = getReposConfig();
  const target = normalizeRepoUrl(url);
  const idx = cfg.repos.findIndex((r) => normalizeRepoUrl(r.url) === target);
  if (idx === -1) throw new Error(`Repo not found: ${url}`);
  cfg.repos.splice(idx, 1);
  saveReposConfig(cfg);
  return cfg;
}

// ---------------------------------------------------------------------------
// Other configs (simple read/write)
// ---------------------------------------------------------------------------

export function getBudgetsConfig(): BudgetsConfig {
  return readYaml<BudgetsConfig>(PATHS.budgets);
}
export function saveBudgetsConfig(cfg: BudgetsConfig): void {
  if (!cfg.version) cfg.version = 1;
  writeYaml(PATHS.budgets, cfg);
}

export function getPoliciesConfig(): PoliciesConfig {
  return readYaml<PoliciesConfig>(PATHS.policies);
}
export function savePoliciesConfig(cfg: PoliciesConfig): void {
  if (!cfg.version) cfg.version = 1;
  writeYaml(PATHS.policies, cfg);
}

export function getModelPricesConfig(): ModelPricesConfig {
  return readYaml<ModelPricesConfig>(PATHS.modelPrices);
}
export function saveModelPricesConfig(cfg: ModelPricesConfig): void {
  if (!cfg.version) cfg.version = 1;
  if (!cfg.prices || typeof cfg.prices !== "object") cfg.prices = {};
  writeYaml(PATHS.modelPrices, cfg);
}

// ---------------------------------------------------------------------------
// GitHub repo probe — detects public/private and fetches metadata
// ---------------------------------------------------------------------------

export interface RepoProbeResult {
  url: string;
  owner: string;
  repo: string;
  exists: boolean;
  visibility: "public" | "private" | "unknown";
  default_branch?: string;
  description?: string;
  stars?: number;
  has_issues?: boolean;
  permissions?: {
    admin?: boolean;
    push?: boolean;
    pull?: boolean;
  };
  warning?: string;
}

export async function probeGitHubRepo(url: string): Promise<RepoProbeResult> {
  const { owner, repo } = extractOwnerRepo(url);
  const normalized = normalizeRepoUrl(url);
  const pat = process.env.GITHUB_PAT;

  const headers: Record<string, string> = {
    Accept: "application/vnd.github+json",
    "User-Agent": "tac-master-dashboard/0.1",
    "X-GitHub-Api-Version": "2022-11-28",
  };
  if (pat) headers.Authorization = `Bearer ${pat}`;

  // First try authenticated — gives us visibility + permissions for private repos
  // the bot has been granted access to
  try {
    const resp = await fetch(`https://api.github.com/repos/${owner}/${repo}`, { headers });
    if (resp.status === 404) {
      // Either truly missing OR private and bot has no access
      return {
        url: normalized,
        owner,
        repo,
        exists: false,
        visibility: "unknown",
        warning:
          "Repository not found with authenticated request. If it's private, " +
          "the bot account (krypto-agent / CleoAgent) needs to be added as a collaborator.",
      };
    }
    if (!resp.ok) {
      return {
        url: normalized,
        owner,
        repo,
        exists: false,
        visibility: "unknown",
        warning: `GitHub API returned HTTP ${resp.status}: ${resp.statusText}`,
      };
    }
    const data: any = await resp.json();
    return {
      url: normalized,
      owner,
      repo,
      exists: true,
      visibility: data.private ? "private" : "public",
      default_branch: data.default_branch,
      description: data.description ?? undefined,
      stars: data.stargazers_count,
      has_issues: data.has_issues,
      permissions: data.permissions,
      warning: data.private && !data.permissions?.push
        ? "Private repo — bot has read-only access. Grant write/admin to allow PR creation."
        : undefined,
    };
  } catch (e: any) {
    return {
      url: normalized,
      owner,
      repo,
      exists: false,
      visibility: "unknown",
      warning: `Network error contacting GitHub: ${e?.message ?? e}`,
    };
  }
}

// ---------------------------------------------------------------------------
// Daemon reload
// ---------------------------------------------------------------------------

/**
 * Retry a failed/aborted issue by invoking orchestrator/ops.py via uv run.
 *
 * The Python module is the single source of truth for guard logic (status
 * checks, audit events). This function is a thin Bun shim that spawns the
 * subprocess and surfaces the result as structured JSON.
 *
 * @task T012
 * @epic T004
 * @why Dashboard endpoint must share the same guard logic as the CLI to
 *      prevent double-dispatch or retrying an already-running issue.
 * @what Shells out to `uv run orchestrator/ops.py retry <issue> <repo>` and
 *      parses stdout/stderr into a typed result object.
 */
export async function retryIssue(
  issueNumber: number,
  repoUrl: string,
): Promise<{ ok: boolean; message: string }> {
  const tacHome = process.env.TAC_MASTER_HOME ?? "/srv/tac-master";
  const opsScript = `${tacHome}/orchestrator/ops.py`;

  try {
    const proc = Bun.spawn(
      ["uv", "run", opsScript, "retry", String(issueNumber), repoUrl],
      {
        cwd: tacHome,
        stdout: "pipe",
        stderr: "pipe",
        env: { ...process.env, TAC_MASTER_HOME: tacHome },
      },
    );
    await proc.exited;
    const stdout = await new Response(proc.stdout).text();
    const stderr = await new Response(proc.stderr).text();
    const combined = (stdout + (stderr ? `\n${stderr}` : "")).trim();

    if (proc.exitCode === 0) {
      return { ok: true, message: combined || `Issue #${issueNumber} queued for retry.` };
    }
    return { ok: false, message: combined || `ops.py exited with code ${proc.exitCode}` };
  } catch (e: any) {
    return { ok: false, message: String(e?.message ?? e) };
  }
}

export async function restartDaemon(service = "tac-master"): Promise<{
  ok: boolean;
  output: string;
}> {
  const allowed = ["tac-master", "tac-master-webhook"];
  if (!allowed.includes(service)) {
    return { ok: false, output: `service ${service} not in allowlist` };
  }
  try {
    const proc = Bun.spawn(["sudo", "-n", "/usr/bin/systemctl", "restart", service], {
      stdout: "pipe",
      stderr: "pipe",
    });
    await proc.exited;
    const stdout = await new Response(proc.stdout).text();
    const stderr = await new Response(proc.stderr).text();
    return {
      ok: proc.exitCode === 0,
      output: stdout + (stderr ? `\nstderr: ${stderr}` : ""),
    };
  } catch (e: any) {
    return { ok: false, output: String(e?.message ?? e) };
  }
}
