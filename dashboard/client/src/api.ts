/**
 * Thin API client for the tac-master dashboard.
 *
 * All endpoints are same-origin (the Bun server serves both the Vue SPA
 * and the JSON API from the same port), so we use relative paths.
 */

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

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const init: RequestInit = { method };
  if (body !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }
  const resp = await fetch(path, init);
  const text = await resp.text();
  let data: any;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { raw: text };
  }
  if (!resp.ok) {
    throw new Error(
      data?.error ?? `${method} ${path} failed: ${resp.status} ${resp.statusText}`,
    );
  }
  return data as T;
}

export const api = {
  // Repos config
  getRepos: () => request<ReposConfig>("GET", "/api/config/repos"),
  putRepos: (cfg: ReposConfig) =>
    request<{ ok: boolean; repos: number }>("PUT", "/api/config/repos", cfg),
  addRepo: (entry: RepoEntry) =>
    request<{ ok: boolean; repos: RepoEntry[] }>("POST", "/api/config/repos", entry),
  updateRepo: (url: string, patch: Partial<RepoEntry>) =>
    request<{ ok: boolean; repos: RepoEntry[] }>(
      "PATCH",
      `/api/config/repos?url=${encodeURIComponent(url)}`,
      patch,
    ),
  deleteRepo: (url: string) =>
    request<{ ok: boolean; repos: RepoEntry[] }>(
      "DELETE",
      `/api/config/repos?url=${encodeURIComponent(url)}`,
    ),

  // Budgets
  getBudgets: () => request<BudgetsConfig>("GET", "/api/config/budgets"),
  putBudgets: (cfg: BudgetsConfig) =>
    request<{ ok: boolean }>("PUT", "/api/config/budgets", cfg),

  // Policies
  getPolicies: () => request<PoliciesConfig>("GET", "/api/config/policies"),
  putPolicies: (cfg: PoliciesConfig) =>
    request<{ ok: boolean }>("PUT", "/api/config/policies", cfg),

  // Model prices
  getModelPrices: () => request<ModelPricesConfig>("GET", "/api/config/model-prices"),
  putModelPrices: (cfg: ModelPricesConfig) =>
    request<{ ok: boolean }>("PUT", "/api/config/model-prices", cfg),

  // GitHub repo probe
  probeRepo: (url: string) =>
    request<RepoProbeResult>(
      "GET",
      `/api/github/repo-info?url=${encodeURIComponent(url)}`,
    ),

  // Daemon control
  restartDaemon: (service = "tac-master") =>
    request<{ ok: boolean; output: string }>(
      "POST",
      "/api/system/restart-daemon",
      { service },
    ),
};
