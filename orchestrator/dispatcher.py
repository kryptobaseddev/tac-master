"""Issue dispatcher.

Polls each allowlisted repo for issues that need work, decides whether to
act, and spawns a Lead subprocess (one of the tac-7 adw_*_iso.py scripts)
inside the target repo's clone.

Trigger rules:
  - new_issue:   an open issue with no comments → dispatch
  - comment_adw: latest comment is exactly "adw" → dispatch
  - label:       any trigger_labels matches an open issue → dispatch

The Lead process itself spawns its own Workers via the tac-7 orchestrator
scripts (adw_sdlc_iso.py etc.). The dispatcher only launches the Lead and
tracks its lifecycle via the state store.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import subprocess
from pathlib import Path

from .budget import BudgetEnforcer
from .config import RepoConfig, TacMasterConfig
from .github_client import GitHubClient, Issue
from .knowledge import KnowledgeBase
from .repo_manager import RepoHandle, RepoManager
from .runner import RunSpec, make_runner
from .state_store import StateStore
from .token_tracker import TokenTracker

log = logging.getLogger(__name__)


# Map workflow name → tac-7 script filename
WORKFLOW_SCRIPTS = {
    "patch": "adw_patch_iso.py",
    "plan_build": "adw_plan_build_iso.py",
    "plan_build_test": "adw_plan_build_test_iso.py",
    "plan_build_test_review": "adw_plan_build_test_review_iso.py",
    "sdlc": "adw_sdlc_iso.py",
    "sdlc_zte": "adw_sdlc_zte_iso.py",
}

# Terminal issue statuses that must NOT re-dispatch
_TERMINAL_ISSUE_STATUSES = {
    "dispatched", "completed", "failed", "aborted", "skipped",
}

# Minimum seconds between retries of a terminally-failed issue. 1 hour.
# After this cooldown, a new comment "adw" or a new label add will still
# re-qualify the issue — only the automatic re-dispatch loop is blocked.
_FAILED_COOLDOWN_SECONDS = 3600

# Maximum wall-clock seconds a run may remain in the "running" state.
# Last-resort guard against stuck subprocess trees. Raised from 1200 after
# session 2026-04-09 — legitimate test auto-fix work (all_backend_tests) was
# being killed mid-turn while Claude was actively resolving failures. The
# stdin/stderr deadlock bugs that necessitated the original 20-min value are
# fixed; this is now purely a backstop for truly wedged processes.
# Related: T025, run d5ab7f05 (killed at 1220s mid-auto-fix turn 1).
_RUN_HANG_TIMEOUT_SECONDS = 60 * 60  # 60 minutes


class Dispatcher:
    def __init__(
        self,
        cfg: TacMasterConfig,
        store: StateStore,
        repo_mgr: RepoManager,
        gh: GitHubClient,
        budget: BudgetEnforcer,
        tokens: TokenTracker | None = None,
    ):
        self.cfg = cfg
        self.store = store
        self.repo_mgr = repo_mgr
        self.gh = gh
        self.budget = budget
        self.tokens = tokens or TokenTracker(
            store, cfg.home / "config" / "model_prices.yaml"
        )
        self.kb = KnowledgeBase(store)

    # ------------------------------------------------------------------
    def poll_once(self) -> int:
        """Poll all allowlisted repos once. Returns number of dispatches made."""
        dispatched = 0
        for repo in self.cfg.repos.repos:
            try:
                dispatched += self._poll_repo(repo)
            except Exception as e:
                log.exception("Error polling %s: %s", repo.url, e)
                self.store.record_event(
                    "error",
                    json.dumps({"stage": "poll", "repo": repo.url, "err": str(e)}),
                    repo_url=repo.url,
                )
        return dispatched

    # ------------------------------------------------------------------
    def _poll_repo(self, repo: RepoConfig) -> int:
        log.debug("Polling %s", repo.url)
        self.store.upsert_repo(
            repo.url, repo.slug, repo.self, repo.default_workflow,
            repo.model_set, repo.auto_merge,
        )
        self.store.mark_polled(repo.url)

        # Fetch ALL open issues (no server-side label filter). GitHub's
        # `labels=a,b` parameter is AND, not OR — passing it would require
        # issues to have every trigger label simultaneously, which is almost
        # never what you want. _should_dispatch() does OR matching on labels
        # client-side using `any(l in repo.trigger_labels for l in issue.labels)`.
        issues = self.gh.list_open_issues(repo.url)

        dispatched = 0
        for issue in issues:
            if self._should_dispatch(repo, issue):
                if self._dispatch(repo, issue):
                    dispatched += 1
        return dispatched

    # ------------------------------------------------------------------
    def _should_dispatch(self, repo: RepoConfig, issue: Issue) -> bool:
        last_comment_id = None
        reason = None
        comment_is_adw = False

        if "new_issue" in repo.triggers and issue.comments_count == 0:
            reason = "new_issue"

        if "label" in repo.triggers and any(lbl in repo.trigger_labels for lbl in issue.labels):
            reason = reason or "label"

        if "comment_adw" in repo.triggers and issue.comments_count > 0:
            comments = self.gh.list_comments(repo.url, issue.number)
            if comments:
                last = comments[-1]
                last_comment_id = last.id
                if last.body.strip().lower() == "adw":
                    reason = reason or "comment_adw"
                    comment_is_adw = True

        current_status = self.store.seen_issue(
            repo.url, issue.number, issue.title, last_comment_id
        )

        if reason is None:
            return False

        # Terminal statuses block automatic re-dispatch. The only way to
        # re-run a failed issue is an explicit "adw" comment (which updates
        # last_comment_id and short-circuits this check).
        if current_status in _TERMINAL_ISSUE_STATUSES:
            if comment_is_adw:
                log.info("Issue %s#%d terminal but new adw comment → retry",
                         repo.url, issue.number)
                return True

            # Special case: failed/aborted with enough cooldown AND a new
            # signal (new comment_id or new label) → allow retry.
            # For now, block all automatic retries of terminal issues.
            log.debug("Issue %s#%d already %s, skipping",
                      repo.url, issue.number, current_status)
            return False

        log.info("Issue %s#%d qualifies (%s)", repo.url, issue.number, reason)
        return True

    # ------------------------------------------------------------------
    def _dispatch(self, repo: RepoConfig, issue: Issue) -> bool:
        # Budget pre-flight
        decision = self.budget.can_dispatch(repo.url)
        if not decision:
            log.warning("Dispatch refused for %s#%d: %s",
                        repo.url, issue.number, decision.reason)
            self.store.record_event(
                "budget",
                json.dumps({"repo": repo.url, "issue": issue.number,
                            "reason": decision.reason}),
                repo_url=repo.url,
            )
            return False

        workflow = repo.default_workflow
        script = WORKFLOW_SCRIPTS.get(workflow)
        if not script:
            log.error("Unknown workflow %r for %s", workflow, repo.url)
            return False

        # Ensure clone + substrate + fetch latest
        handle: RepoHandle = self.repo_mgr.ensure_clone(repo.url, repo.fs_slug)
        self.repo_mgr.sync(handle)

        # Allocate ADW ID (8 hex chars, matching tac-7 format)
        adw_id = secrets.token_hex(4)

        # NOTE: we do NOT pre-create the worktree here. The tac-7 ADW's
        # own worktree_ops.create_worktree() computes project_root relative
        # to the ADW script's __file__ and expects worktrees at
        # <clone>/trees/<adw_id>/. Creating them elsewhere and symlinking
        # confuses git's worktree tracking and causes validate_worktree
        # to fail. Let the ADW handle it natively — we just track the
        # path after the fact in reap_finished_runs.
        wt_path = handle.clone_path / "trees" / adw_id

        self.store.create_run(adw_id, repo.url, issue.number, workflow, repo.model_set)
        self.store.update_run(adw_id, worktree_path=str(wt_path), status="running")
        self.budget.record_dispatch(repo.url)

        # Inject top-K relevant lessons into the worktree as a prompt tail
        # for the Lead's classifier/planner to discover.
        try:
            lessons = self.kb.fetch_relevant(issue.title, repo.url, k=3)
            if lessons:
                tail_path = self.kb.write_prompt_tail(wt_path, lessons)
                log.info("Injected %d lessons into %s", len(lessons), tail_path)
                self.store.record_event(
                    "knowledge",
                    json.dumps({
                        "adw_id": adw_id,
                        "lesson_count": len(lessons),
                        "lesson_adws": [lesson.adw_id for lesson in lessons],
                    }),
                    repo_url=repo.url,
                    adw_id=adw_id,
                )
        except Exception as e:
            log.warning("Knowledge injection failed (non-fatal): %s", e)

        env = self._build_env(repo, handle)
        log_file = self.cfg.logs_dir / f"run_{adw_id}.log"

        log.info("Dispatching %s for %s#%d (adw_id=%s) via %s [runtime=%s]",
                 workflow, repo.url, issue.number, adw_id, script, repo.runtime)

        # Build the run spec and hand off to the appropriate runner.
        # Native: subprocess.Popen inside the clone.
        # Podman: rootless container with worktree + substrate bind-mounted.
        spec = RunSpec(
            adw_id=adw_id,
            repo_url=repo.url,
            issue_number=issue.number,
            workflow=script,
            clone_path=handle.clone_path,
            worktree_path=wt_path,
            env=env,
            log_file=log_file,
            substrate_home=self.cfg.home,
            container_image=repo.container_image,
        )
        runner = make_runner(repo.runtime, default_image=repo.container_image)
        try:
            pid = runner.spawn(spec)
        except Exception as e:
            log.exception("Runner failed for %s#%d: %s", repo.url, issue.number, e)
            self.store.update_run(adw_id, status="failed", ended_at=_now())
            self.store.record_event(
                "error",
                json.dumps({"stage": "runner.spawn", "adw_id": adw_id,
                            "runtime": repo.runtime, "err": str(e)}),
                repo_url=repo.url,
                adw_id=adw_id,
            )
            return False

        self.store.update_run(adw_id, pid=pid)
        self.store.set_issue_status(repo.url, issue.number, "dispatched")
        self.store.record_event(
            "dispatch",
            json.dumps({
                "repo": repo.url,
                "issue": issue.number,
                "workflow": workflow,
                "adw_id": adw_id,
                "runtime": repo.runtime,
                "pid": pid,
                "log": str(log_file),
            }),
            repo_url=repo.url,
            adw_id=adw_id,
        )
        return True

    # ------------------------------------------------------------------
    def _build_env(self, repo: RepoConfig, handle: RepoHandle) -> dict[str, str]:
        env = os.environ.copy()
        # NOTE: identity.get(key, default) only uses `default` if the key is
        # MISSING, not if the value is an empty string. The loader stores
        # every expected key with os.getenv(k, "") so empty strings are
        # common. Use `or` to fall back on empty values too.
        env.update({
            "GITHUB_REPO_URL": repo.url,
            "GITHUB_PAT": self.cfg.identity.get("GITHUB_PAT") or "",
            "ANTHROPIC_API_KEY": self.cfg.identity.get("ANTHROPIC_API_KEY") or "",
            "CLAUDE_CODE_PATH": self.cfg.identity.get("CLAUDE_CODE_PATH") or "claude",
            "ADW_MODEL_SET": repo.model_set,
            "TAC_MASTER_HOME": str(self.cfg.home),
            # Force line-buffered Python output so run_<adw_id>.log shows
            # progress in real time instead of only flushing on process exit.
            # Without this, the log file appears frozen at "Allocated ports"
            # for the entire duration of the run.
            "PYTHONUNBUFFERED": "1",
            # Tell the dashboard event hook where to POST. Defaults to
            # http://localhost:4000/events which works from inside the LXC.
            "TAC_DASHBOARD_URL": env.get("TAC_DASHBOARD_URL") or "http://localhost:4000/events",
        })
        # Per-repo env overrides
        for k, v in repo.env.items():
            env[k] = str(v)
        return env

    # ------------------------------------------------------------------
    def reap_finished_runs(self) -> None:
        """Check active runs for completion. Called from the daemon loop.

        Two-way reap:
          1. Natural finish: pid gone → infer status from state.json
          2. Hang detection: pid alive but run has been in "running" state
             for more than _RUN_HANG_TIMEOUT_SECONDS → SIGKILL and fail.
        """
        import time as _time
        now = int(_time.time())
        for run in self.store.list_active_runs():
            pid = run.get("pid")
            if not pid:
                continue

            pid_alive = True
            try:
                os.kill(pid, 0)  # probe
            except ProcessLookupError:
                pid_alive = False
            except PermissionError:
                continue

            if pid_alive:
                # Check for hang
                started = run.get("started_at") or 0
                if now - started > _RUN_HANG_TIMEOUT_SECONDS:
                    log.warning(
                        "Run %s has been running for %ds > %ds — killing as hang",
                        run["adw_id"], now - started, _RUN_HANG_TIMEOUT_SECONDS,
                    )
                    self._kill_run_tree(pid, run["adw_id"])
                    self.store.update_run(
                        run["adw_id"], status="failed", ended_at=now,
                    )
                    self.store.set_issue_status(
                        run["repo_url"], run["issue_number"], "failed",
                    )
                    self.store.record_event(
                        "error",
                        json.dumps({
                            "adw_id": run["adw_id"],
                            "reason": "hang_timeout",
                            "elapsed_s": now - started,
                        }),
                        repo_url=run["repo_url"],
                        adw_id=run["adw_id"],
                    )
                continue  # still running (or just killed)

            # Process gone; mark finished. We don't know the exit code cleanly
            # because we spawned detached — the log file is the source of truth.
            # The Lead process writes adw_state.json before exit; inspect it
            # to get final status.
            adw_id = run["adw_id"]
            final_status = self._infer_final_status(run)
            log.info("Run %s finished: %s", adw_id, final_status)

            # Attribute tokens + cost before marking the run finished so
            # budget counters are accurate at dispatch decision time.
            wt = run.get("worktree_path")
            if wt:
                try:
                    usage = self.tokens.attribute_run(
                        adw_id, Path(wt), run["repo_url"])
                    log.info(
                        "Tokens adw=%s tokens=%d cost=$%.4f",
                        adw_id, usage.total_tokens, usage.cost_usd,
                    )
                except Exception as e:
                    log.exception("Token attribution failed for %s: %s", adw_id, e)

            self.store.update_run(adw_id, status=final_status, ended_at=_now())
            self.store.set_issue_status(
                run["repo_url"], run["issue_number"],
                "completed" if final_status == "succeeded" else "failed",
            )
            self.store.record_event(
                "phase_end",
                json.dumps({"adw_id": adw_id, "final": final_status}),
                repo_url=run["repo_url"],
                adw_id=adw_id,
            )

            # Run reflection (self-improvement lesson writer) — fire and forget.
            # Failures here should not block the daemon or reap cycle.
            try:
                self._run_reflect(adw_id, run)
            except Exception as e:
                log.warning("Reflect failed for %s: %s", adw_id, e)

    def _kill_run_tree(self, pid: int, adw_id: str) -> None:
        """SIGKILL a run's entire process tree.

        Uses pgrep to find children recursively so the daemon doesn't
        leave zombie claude subprocesses.
        """
        import signal
        try:
            # Find the whole session/process group. We spawned with
            # start_new_session=True so pid is a session leader.
            os.killpg(os.getpgid(pid), signal.SIGKILL)
            log.info("Killed process group for pid %d (adw=%s)", pid, adw_id)
        except (ProcessLookupError, PermissionError) as e:
            log.debug("Could not kill pg for pid %d: %s", pid, e)
            # Fallback: try just the main pid
            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:
                pass

    def _run_reflect(self, adw_id: str, run: dict) -> None:
        """Spawn adw_reflect_iso to write a lesson for this finished run."""
        import os
        wt = run.get("worktree_path")
        if not wt:
            return
        script = Path(wt) / "adws" / "adw_reflect_iso.py"
        if not script.exists():
            # Fallback: use tac-master's own copy
            script = self.cfg.home / "adws" / "adw_reflect_iso.py"
        if not script.exists():
            return
        env = os.environ.copy()
        env["TAC_MASTER_HOME"] = str(self.cfg.home)
        env["GITHUB_REPO_URL"] = run["repo_url"]
        subprocess.Popen(
            ["uv", "run", str(script), adw_id],
            cwd=wt,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def _infer_final_status(self, run: dict) -> str:
        """Peek at the ADW's state.json to see if it reached the ship phase.

        tac-7's ADWs write state to <project_root>/agents/<adw_id>/adw_state.json
        where project_root is 3 dirs up from adw_modules/state.py. When the
        ADW is invoked from inside our cloned repo (with adws/ as a physical
        copy of tac-master's adws/ — see repo_manager._inject_substrate), that
        copy's __file__ resolves to the clone path, so that's where we look.

        T010 / T002 content guard: we distinguish three outcomes:
          - "succeeded"  — state file exists AND has both plan_file and all_adws
          - "incomplete" — state file exists but is missing plan_file or all_adws;
                           this means the ADW crashed or was killed mid-flight
                           before populating those fields. It is NOT a confirmed
                           failure — the run may have succeeded but state was
                           never persisted completely. Operators should inspect
                           the clone's agents/<adw_id>/ directory for logs.
          - "failed"     — no state file found under any candidate path; the ADW
                           never started or exited before its first save().

        @task T010
        @epic T002
        @why _infer_final_status was returning "failed" for states missing
             plan_file/all_adws — a content bug masked as a confirmed failure.
        @what Guards incomplete state files from being mis-classified as failed
             runs by returning a distinct "incomplete" status.
        """
        adw_id = run["adw_id"]
        repo_url = run.get("repo_url", "")

        # Primary: state file in the clone (where the ADW actually writes it).
        # Path is identical to the ADWState.save() write path for NativeRunner
        # (verified by T006 investigation — no mismatch for default flow).
        fs_slug = repo_url.replace("https://github.com/", "").replace(".git", "").replace("/", "_")
        clone_state = self.cfg.repos_dir / fs_slug / "agents" / adw_id / "adw_state.json"
        candidates = [clone_state]

        # Legacy: older runs may have state under the worktree path
        wt = run.get("worktree_path")
        if wt:
            candidates.append(Path(wt) / "agents" / adw_id / "adw_state.json")
            candidates.append(Path(wt).parent.parent / "agents" / adw_id / "adw_state.json")

        for state_path in candidates:
            if state_path.exists():
                try:
                    data = json.loads(state_path.read_text())
                    if data.get("plan_file") and data.get("all_adws"):
                        return "succeeded"
                    # State file exists but required fields are absent — the ADW
                    # likely crashed before its final save(). Return "incomplete"
                    # rather than "failed" so callers can distinguish a partial
                    # write from a run that truly errored out end-to-end.
                    log.warning(
                        "ADW %s: state file found at %s but plan_file=%r all_adws=%r — "
                        "run appears incomplete (crashed mid-flight or never reached ship phase). "
                        "Inspect agents/%s/ for run logs.",
                        adw_id, state_path,
                        data.get("plan_file"), data.get("all_adws"),
                        adw_id,
                    )
                    return "incomplete"
                except Exception:
                    continue

        return "failed"


def _now() -> int:
    import time
    return int(time.time())
