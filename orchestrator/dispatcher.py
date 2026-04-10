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
import re
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

        if "label" in repo.triggers and any(label in repo.trigger_labels for label in issue.labels):
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

        # Extract CLEO task ID from issue title (if present)
        cleo_task_id = self._extract_cleo_task_id(issue.title)

        self.store.create_run(adw_id, repo.url, issue.number, workflow, repo.model_set, cleo_task_id)
        self.store.update_run(adw_id, worktree_path=str(wt_path), status="running")
        self.budget.record_dispatch(repo.url)

        # Propagate cleo_task_id to agent_instances if present.
        # agent_instances rows for this adw_id may not exist yet (they are
        # created by the orchestrator service), but set_agent_instance_cleo_task_id
        # is idempotent — it does a best-effort UPDATE that is a no-op if no
        # rows exist yet. The important mapping lives in runs.cleo_task_id.
        if cleo_task_id:
            try:
                self.store.set_agent_instance_cleo_task_id(adw_id, cleo_task_id)
            except Exception as e:
                log.debug(
                    "set_agent_instance_cleo_task_id failed (non-blocking): %s", e
                )

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

        # Inject CLEO task context (acceptance criteria, task ID) into worktree
        # This is non-blocking — failures should not prevent dispatch.
        self._inject_cleo_context(issue.title, issue.body, wt_path)

        env = self._build_env(repo, handle, cleo_task_id)
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
    def _extract_cleo_task_id(self, issue_title: str) -> str | None:
        """Extract CLEO task ID from issue title.

        Matches the pattern [TXXX] at the start of the title.
        Returns the task ID (e.g., "T084") or None if not found.
        """
        match = re.match(r'\[?(T\d+)\]?', issue_title)
        if match:
            return match.group(1)
        return None

    def _fetch_cleo_task_details(self, cleo_task_id: str) -> dict:
        """Fetch CLEO task details via `cleo show --json` for the given task ID.

        Returns a dict with keys: title, parentId, depends, acceptance, size, priority.
        Returns an empty dict on failure (non-blocking caller pattern).
        """
        try:
            result = subprocess.run(
                ["cleo", "show", cleo_task_id, "--json"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                import json as _json
                data = _json.loads(result.stdout)
                # cleo show --json wraps the task under data.task
                task = data.get("data", {}).get("task", data)
                return {
                    "title": task.get("title", ""),
                    "description": task.get("description", ""),
                    "parentId": task.get("parentId"),
                    "depends": task.get("depends") or [],
                    "acceptance": task.get("acceptance") or [],
                    "size": task.get("size", ""),
                    "priority": task.get("priority", ""),
                }
        except Exception as e:  # noqa: BLE001
            log.debug("cleo show %s failed (non-blocking): %s", cleo_task_id, e)
        return {}

    def _fetch_cleo_epic_details(self, epic_id: str) -> dict:
        """Fetch CLEO epic details via `cleo show --json` for the given epic ID.

        Returns a dict with keys: title, status.
        Returns an empty dict on failure (non-blocking caller pattern).
        """
        try:
            result = subprocess.run(
                ["cleo", "show", epic_id, "--json"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                import json as _json
                data = _json.loads(result.stdout)
                task = data.get("data", {}).get("task", data)
                return {
                    "title": task.get("title", ""),
                    "status": task.get("status", ""),
                }
        except Exception as e:  # noqa: BLE001
            log.debug("cleo show %s (epic) failed (non-blocking): %s", epic_id, e)
        return {}

    def _fetch_cleo_dep_statuses(self, dep_ids: list[str]) -> dict[str, str]:
        """Fetch status for each dependency task ID.

        Returns a mapping of task_id → status string.
        Missing/failed lookups are omitted from the result.
        """
        statuses: dict[str, str] = {}
        for dep_id in dep_ids:
            try:
                result = subprocess.run(
                    ["cleo", "show", dep_id, "--json"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0 and result.stdout.strip():
                    import json as _json
                    data = _json.loads(result.stdout)
                    task = data.get("data", {}).get("task", data)
                    statuses[dep_id] = task.get("status", "unknown")
            except Exception as e:  # noqa: BLE001
                log.debug("cleo show %s (dep) failed (non-blocking): %s", dep_id, e)
        return statuses

    def _inject_cleo_context(self, issue_title: str, issue_body: str, wt_path: Path) -> None:
        """Inject CLEO task context into the worktree as .cleo_context.md.

        Extracts:
          - CLEO task ID from issue title [TXXX] prefix
          - Acceptance criteria from issue body (- [ ] lines) and/or cleo show
          - Parent epic name and status (via cleo show on parentId)
          - Dependency statuses (done vs pending, via cleo show on each dep)

        Writes .cleo_context.md to worktree root.
        This is non-blocking: failures here should not prevent dispatch.
        """
        try:
            cleo_task_id = self._extract_cleo_task_id(issue_title)
            if not cleo_task_id:
                # No CLEO task ID found — skip injection
                return

            # Strip [TXXX] prefix from issue title for the display title
            title_match = re.match(r'\[?T\d+\]?\s*(.*)', issue_title)
            display_title = title_match.group(1) if title_match else issue_title

            # Fetch richer task details from cleo CLI
            task_details = self._fetch_cleo_task_details(cleo_task_id)

            # Prefer acceptance criteria from cleo (authoritative source); fall
            # back to parsing the GitHub issue body if cleo returns nothing.
            acceptance_criteria = task_details.get("acceptance") or []
            if not acceptance_criteria and issue_body:
                for line in issue_body.split('\n'):
                    line = line.strip()
                    if line.startswith('- [ ]'):
                        criterion = line[5:].strip()
                        if criterion:
                            acceptance_criteria.append(criterion)

            # Build the criteria list (as markdown checklist)
            if acceptance_criteria:
                criteria_md = "\n".join(f"- [ ] {c}" for c in acceptance_criteria)
            else:
                criteria_md = "(No acceptance criteria specified)"

            # ── Parent epic context ───────────────────────────────────────
            epic_section = ""
            parent_id = task_details.get("parentId")
            if parent_id:
                epic_details = self._fetch_cleo_epic_details(parent_id)
                epic_title = epic_details.get("title", parent_id)
                epic_status = epic_details.get("status", "unknown")
                epic_section = (
                    f"\n## Parent Epic\n\n"
                    f"**Epic ID**: {parent_id}\n"
                    f"**Epic Title**: {epic_title}\n"
                    f"**Epic Status**: {epic_status}\n"
                )

            # ── Dependency status ─────────────────────────────────────────
            dep_section = ""
            dep_ids: list[str] = task_details.get("depends") or []
            if dep_ids:
                dep_statuses = self._fetch_cleo_dep_statuses(dep_ids)
                dep_lines = []
                for dep_id in dep_ids:
                    status = dep_statuses.get(dep_id, "unknown")
                    marker = "done" if status in ("done", "completed") else "pending"
                    dep_lines.append(f"- {dep_id}: {status} [{marker}]")
                dep_section = (
                    "\n## Dependencies\n\n"
                    + "\n".join(dep_lines)
                    + "\n"
                )

            # ── Task metadata ─────────────────────────────────────────────
            priority = task_details.get("priority", "")
            size = task_details.get("size", "")
            meta_parts = []
            if priority:
                meta_parts.append(f"**Priority**: {priority}")
            if size:
                meta_parts.append(f"**Size**: {size}")
            meta_line = "\n".join(meta_parts) + "\n" if meta_parts else ""

            # Build the context file content
            context_content = (
                f"# CLEO Task Context\n\n"
                f"**Task ID**: {cleo_task_id}\n"
                f"**Task Title**: {display_title}\n"
                f"{meta_line}"
                f"{epic_section}"
                f"{dep_section}"
                f"\n## Acceptance Criteria\n\n"
                f"The following acceptance criteria MUST all be satisfied before this task is considered complete:\n\n"
                f"{criteria_md}\n"
                f"\n## Instructions\n\n"
                f"You are working on CLEO task {cleo_task_id}. Your goal is to satisfy all of the above acceptance criteria. When you have completed the work, confirm each criterion is met in your final summary.\n"
            )

            # Write to worktree root
            wt_path.mkdir(parents=True, exist_ok=True)
            context_file = wt_path / ".cleo_context.md"
            context_file.write_text(context_content)
            log.info("Injected CLEO context into %s for task %s", context_file, cleo_task_id)

        except Exception as e:
            log.warning("CLEO context injection failed (non-blocking): %s", e)

    # ------------------------------------------------------------------
    def create_issue_from_task(
        self, task_id: str, repo_config: RepoConfig
    ) -> "Issue | None":
        """Create a GitHub issue from a CLEO task and store the mapping.

        Reads the CLEO task via `cleo show {task_id}`, builds a GitHub issue
        with:
          - Title: "[{task_id}] {task_title}"
          - Body:  task description + acceptance criteria as checkboxes
          - Labels: ["adw"]

        Stores the resulting cleo_task_id on the matching agent_instances row
        (via the runs.adw_id join key) so the mapping is bidirectional.

        Returns the created Issue on success, or None on any failure.
        This method is non-blocking — failures are logged but never raised.
        """
        try:
            task = self._fetch_cleo_task_details(task_id)
            if not task:
                log.warning(
                    "create_issue_from_task: could not fetch CLEO task %s", task_id
                )
                return None

            title = f"[{task_id}] {task.get('title', task_id)}"

            # Build issue body: description then acceptance criteria checkboxes
            description = task.get("description", "").strip()
            acceptance: list[str] = task.get("acceptance") or []

            body_parts: list[str] = []
            if description:
                body_parts.append(description)
            if acceptance:
                body_parts.append("\n## Acceptance Criteria\n")
                body_parts.extend(f"- [ ] {criterion}" for criterion in acceptance)

            body = "\n".join(body_parts) if body_parts else f"CLEO task {task_id}"

            issue = self.gh.create_issue(
                repo_config.url,
                title=title,
                body=body,
                labels=["adw"],
            )
            if issue is None:
                log.warning(
                    "create_issue_from_task: GitHub issue creation failed for %s", task_id
                )
                return None

            log.info(
                "Created GitHub issue %s#%d for CLEO task %s",
                repo_config.url, issue.number, task_id,
            )

            # Record the creation event so the dashboard and reap callbacks can
            # look up cleo_task_id → issue_number.  The full agent_instances
            # mapping (cleo_task_id column) is written at dispatch time by
            # _dispatch() once the adw_id is allocated.
            self.store.record_event(
                "cleo_issue_created",
                json.dumps({
                    "cleo_task_id": task_id,
                    "repo_url": repo_config.url,
                    "issue_number": issue.number,
                    "issue_url": issue.html_url,
                }),
                repo_url=repo_config.url,
            )
            return issue

        except Exception as e:  # noqa: BLE001
            log.warning(
                "create_issue_from_task failed for %s (non-blocking): %s", task_id, e
            )
            return None

    # ------------------------------------------------------------------
    def _create_followup_task(
        self, cleo_task_id: str, adw_id: str, final_status: str
    ) -> None:
        """Create a follow-up CLEO task for a failed or incomplete ADW run.

        Adds a child task under cleo_task_id with:
          - Failed: title "Fix: {original_title}", high priority
          - Incomplete: title "Resume: {original_title}", high priority
          - Labels: ["auto-followup", "fix"]
          - Type: task

        Skips creation if the original task title starts with "Fix:" or "Resume:"
        to prevent infinite follow-up chains.
        Non-blocking — failures are logged as warnings, never raised.
        """
        try:
            # Fetch parent task to get original title
            parent = self._fetch_cleo_task_details(cleo_task_id)
            if not parent:
                log.debug(
                    "_create_followup_task: could not fetch task %s", cleo_task_id
                )
                return

            original_title = parent.get("title", "")

            # Loop guard: don't create follow-ups for tasks that are themselves follow-ups.
            if original_title.startswith("Fix:") or original_title.startswith("Resume:"):
                log.debug(
                    "_create_followup_task: skipping follow-up for %s — "
                    "title already starts with 'Fix:' or 'Resume:'",
                    cleo_task_id,
                )
                return

            # Determine prefix and reason based on final_status
            if final_status == "failed":
                prefix = "Fix"
                reason_text = "ADW run failed"
            else:  # incomplete
                prefix = "Resume"
                reason_text = "ADW run incomplete"

            followup_title = f"{prefix}: {original_title}"

            # Build cleo add command with all required fields
            cmd = [
                "cleo", "add",
                "--parent", cleo_task_id,
                "--title", followup_title,
                "--priority", "high",
                "--type", "task",
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                log.warning(
                    "_create_followup_task: cleo add failed for %s (rc=%d): %s",
                    cleo_task_id, result.returncode, result.stderr.strip(),
                )
                return

            # Extract the new task ID from cleo add output (JSON or last word)
            new_task_id: str | None = None
            try:
                import json as _json
                out_data = _json.loads(result.stdout)
                new_task_id = (
                    out_data.get("data", {}).get("task", {}).get("id")
                    or out_data.get("id")
                )
            except Exception:
                # Non-JSON output — try parsing last token
                tokens = result.stdout.strip().split()
                if tokens:
                    candidate = tokens[-1].strip(".,")
                    if re.match(r"T\d+", candidate):
                        new_task_id = candidate

            # Attempt to add labels to the follow-up task
            if new_task_id:
                try:
                    subprocess.run(
                        [
                            "cleo", "update", new_task_id,
                            "--labels", "auto-followup", "fix",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                except Exception as e:  # noqa: BLE001
                    log.debug(
                        "_create_followup_task: setting labels on %s failed (non-blocking): %s",
                        new_task_id, e,
                    )

            # Append note to parent for traceability
            note = (
                f"Follow-up task created: {new_task_id or '(see cleo)'} "
                f"({reason_text}, ADW {adw_id})"
            )
            try:
                subprocess.run(
                    ["cleo", "update", cleo_task_id, "--note", note],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            except Exception as e:  # noqa: BLE001
                log.debug("_create_followup_task: adding note to %s failed (non-blocking): %s", cleo_task_id, e)

            log.info(
                "Created follow-up task %s (%s: %s) for %s (ADW %s)",
                new_task_id or "(unknown)", prefix, original_title, cleo_task_id, adw_id,
            )

        except Exception as e:  # noqa: BLE001
            log.warning(
                "_create_followup_task failed for %s (non-blocking): %s", cleo_task_id, e
            )

    # ------------------------------------------------------------------
    def _build_env(self, repo: RepoConfig, handle: RepoHandle, cleo_task_id: str | None = None) -> dict[str, str]:
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
        # Add CLEO context env vars if present
        if cleo_task_id:
            env["CLEO_TASK_ID"] = cleo_task_id
            # Fetch task details to extract epic_id and clean title
            task_details = self._fetch_cleo_task_details(cleo_task_id)
            if task_details:
                # Add CLEO_EPIC_ID if the task has a parent
                parent_id = task_details.get("parentId")
                if parent_id:
                    env["CLEO_EPIC_ID"] = parent_id
                # Extract clean title (remove [TXXX] prefix) and add as CLEO_TASK_TITLE
                task_title = task_details.get("title", cleo_task_id)
                if task_title:
                    env["CLEO_TASK_TITLE"] = task_title
        # Per-repo env overrides
        for k, v in repo.env.items():
            env[k] = str(v)
        return env

    # ------------------------------------------------------------------
    def reap_finished_runs(self) -> None:
        """Check active runs for completion. Called from the daemon loop.

        Two-way reap:
          1. Natural finish: pid gone → infer status from state.json
          2. Zombie reap: pid is a zombie (exited but not waited on) →
             call os.waitpid to collect exit status and infer final status.
             This happens when uv run spawns a python child that exits with
             sys.exit(1): uv exits, becomes a zombie because the daemon
             never calls wait(). os.kill(pid, 0) succeeds on zombies, so
             without explicit waitpid the run stays "running" forever.
          3. Hang detection: pid alive but run has been in "running" state
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
                os.kill(pid, 0)  # probe — also succeeds for zombie processes
            except ProcessLookupError:
                pid_alive = False
            except PermissionError:
                continue

            if pid_alive:
                # Attempt a non-blocking wait to reap any zombie.  If the
                # process is still truly running, waitpid returns (0, 0).
                # If it is a zombie (exited but un-reaped), waitpid returns
                # (pid, raw_status) and we can treat it as finished.
                try:
                    reaped_pid, raw_status = os.waitpid(pid, os.WNOHANG)
                    if reaped_pid == pid:
                        # Process was a zombie — now reaped.
                        exit_code = os.waitstatus_to_exitcode(raw_status)
                        log.info(
                            "Reaped zombie pid=%d adw=%s exit_code=%d",
                            pid, run["adw_id"], exit_code,
                        )
                        pid_alive = False  # fall through to finish logic below
                except ChildProcessError:
                    # Not our child (e.g. adopted after re-parent) — ignore
                    pass
                except OSError:
                    pass

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

            # Process gone (or zombie now reaped); mark finished.
            # We don't know the exit code cleanly because we spawned detached
            # — the log file is the source of truth. The Lead process writes
            # adw_state.json before exit; inspect it to get final status.
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

            # Update CLEO task status (if this run is linked to a CLEO task).
            # Fire and forget — callback failure must not block the reap cycle.
            try:
                self._update_cleo_task_status(adw_id, final_status)
            except Exception as e:
                log.warning("CLEO task status update failed for %s: %s", adw_id, e)

            # Create follow-up CLEO task for failed/incomplete runs.
            # Non-blocking — must not crash the reap loop.
            if final_status in ("failed", "incomplete"):
                try:
                    cleo_task_id = self.store.get_cleo_task_id(adw_id)
                    if cleo_task_id:
                        self._create_followup_task(
                            cleo_task_id=cleo_task_id,
                            adw_id=adw_id,
                            final_status=final_status,
                        )
                except Exception as e:
                    log.warning(
                        "Follow-up CLEO task creation failed for %s: %s", adw_id, e
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

    def _update_cleo_task_status(self, adw_id: str, final_status: str) -> None:
        """Update CLEO task status based on ADW final status.

        Maps ADW status to CLEO task status:
          - "succeeded" → `cleo complete <task_id>` (done)
          - "failed" → `cleo update <task_id> --status failed --note "..."`
          - "incomplete" → `cleo update <task_id> --status blocked --note "..."`

        Silently returns if no CLEO task ID is linked (not all runs are CLEO-originated).
        Wraps in try/except so callback failure does NOT crash the reap loop.
        """
        import time as _time
        try:
            cleo_task_id = self.store.get_cleo_task_id(adw_id)
            if not cleo_task_id:
                # Not a CLEO-linked run; silently skip
                return

            now = _time.time()
            timestamp = _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(now))

            if final_status == "succeeded":
                # Happy path: mark the CLEO task as done
                result = subprocess.run(
                    ["cleo", "complete", cleo_task_id],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    log.info(
                        "CLEO callback: marked task %s as done (adw=%s)",
                        cleo_task_id, adw_id,
                    )
                else:
                    log.warning(
                        "CLEO callback: cleo complete %s failed with exit code %d: %s",
                        cleo_task_id, result.returncode, result.stderr,
                    )
            else:
                # Failed or incomplete: use cleo update with descriptive note
                if final_status == "incomplete":
                    cleo_status = "blocked"
                    note_text = (
                        f"ADW run incomplete (may have crashed mid-flight) — "
                        f"inspect agents/{adw_id}/ for logs. "
                        f"[{timestamp}]"
                    )
                else:  # "failed"
                    cleo_status = "failed"
                    note_text = (
                        f"ADW run failed. ADW ID: {adw_id}. "
                        f"[{timestamp}]"
                    )

                result = subprocess.run(
                    [
                        "cleo", "update", cleo_task_id,
                        "--status", cleo_status,
                        "--note", note_text,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    log.info(
                        "CLEO callback: marked task %s as %s with note (adw=%s)",
                        cleo_task_id, cleo_status, adw_id,
                    )
                else:
                    log.warning(
                        "CLEO callback: cleo update %s failed with exit code %d: %s",
                        cleo_task_id, result.returncode, result.stderr,
                    )

        except subprocess.TimeoutExpired:
            log.warning("CLEO callback: cleo CLI timed out for task %s (adw=%s)", cleo_task_id, adw_id)
        except Exception as e:  # noqa: BLE001
            # Catch all exceptions to prevent callback failure from breaking the reap loop
            log.warning("CLEO callback failed for adw=%s: %s", adw_id, e)

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
