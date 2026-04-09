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
import sys
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

        if "new_issue" in repo.triggers and issue.comments_count == 0:
            reason = "new_issue"

        if "label" in repo.triggers and any(l in repo.trigger_labels for l in issue.labels):
            reason = reason or "label"

        if "comment_adw" in repo.triggers and issue.comments_count > 0:
            comments = self.gh.list_comments(repo.url, issue.number)
            if comments:
                last = comments[-1]
                last_comment_id = last.id
                if last.body.strip().lower() == "adw":
                    reason = reason or "comment_adw"

        current_status = self.store.seen_issue(
            repo.url, issue.number, issue.title, last_comment_id
        )

        if reason is None:
            return False
        if current_status in ("dispatched", "completed"):
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

        # Pre-create the worktree (optional — tac-7 ADWs can create their own,
        # but pre-creating lets us inject substrate before the ADW boots.
        # Passing branch_name=None lets the ADW rename after classification.)
        wt_path = self.repo_mgr.create_worktree(handle, adw_id)

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
                        "lesson_adws": [l.adw_id for l in lessons],
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
        })
        # Per-repo env overrides
        for k, v in repo.env.items():
            env[k] = str(v)
        return env

    # ------------------------------------------------------------------
    def reap_finished_runs(self) -> None:
        """Check active runs for completion. Called from the daemon loop."""
        for run in self.store.list_active_runs():
            pid = run.get("pid")
            if not pid:
                continue
            try:
                os.kill(pid, 0)  # probe
                continue  # still running
            except ProcessLookupError:
                pass
            except PermissionError:
                continue

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

    def _run_reflect(self, adw_id: str, run: dict) -> None:
        """Spawn adw_reflect_iso to write a lesson for this finished run."""
        import os
        import subprocess
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
        """Peek at the ADW's state.json to see if it reached the ship phase."""
        wt = run.get("worktree_path")
        if not wt:
            return "failed"
        state_path = Path(wt) / "agents" / run["adw_id"] / "adw_state.json"
        if not state_path.exists():
            return "failed"
        try:
            data = json.loads(state_path.read_text())
            # Presence of all expected phase artifacts → succeeded
            if data.get("plan_file") and data.get("all_adws"):
                return "succeeded"
        except Exception:
            pass
        return "failed"


def _now() -> int:
    import time
    return int(time.time())
