"""Repository lifecycle manager for tac-master.

Responsibilities:
  1. Clone allowlisted repos on first use into <home>/repos/<owner>_<repo>/
  2. Fetch latest before each dispatch
  3. Inject tac-master substrate (adws/, .claude/) into each clone via symlinks
  4. Isolate substrate from the target repo's git index using .git/info/exclude
  5. Pre-create git worktrees at <home>/trees/<owner>_<repo>__<adw_id>/
  6. Inject substrate into each worktree too (symlinks survive worktree creation
     only when added after the fact)
  7. Clean up worktrees on completion

Design note on substrate injection:
  tac-7's ADW scripts assume they live inside the target project at <root>/adws/.
  We preserve that assumption by symlinking tac-master's substrate into each
  clone and each worktree. Since the symlinks are excluded via .git/info/exclude,
  they never show up in diffs or PRs created by the ADWs.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

SUBSTRATE_DIRS = ("adws", ".claude", "ai_docs")
# These paths are excluded in each clone so ADW runtime state is never committed
# back to target repos.
RUNTIME_EXCLUDES = (
    "adws",
    ".claude",
    "ai_docs",
    "agents/",
    "trees/",
    "logs/",
    ".ports.env",
)


@dataclass
class RepoHandle:
    """Live handle to a cloned target repo."""

    url: str
    slug: str              # owner/repo
    fs_slug: str           # owner_repo
    clone_path: Path

    @property
    def git_dir(self) -> Path:
        return self.clone_path / ".git"


class RepoManager:
    def __init__(self, home: Path, repos_dir: Path, trees_dir: Path,
                 identity: dict[str, str]):
        self.home = home
        self.repos_dir = repos_dir
        self.trees_dir = trees_dir
        self.identity = identity
        self.repos_dir.mkdir(parents=True, exist_ok=True)
        self.trees_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Clone lifecycle
    # ------------------------------------------------------------------

    def ensure_clone(self, url: str, fs_slug: str) -> RepoHandle:
        """Clone repo if not already present, return handle."""
        clone_path = self.repos_dir / fs_slug
        slug = fs_slug.replace("_", "/", 1)  # best-effort display slug

        if not clone_path.exists():
            log.info("Cloning %s → %s", url, clone_path)
            auth_url = self._authed_url(url)
            subprocess.run(
                ["git", "clone", auth_url, str(clone_path)],
                check=True,
                env=self._git_env(),
            )
            self._configure_identity(clone_path)
            self._inject_substrate(clone_path)
        else:
            log.debug("Clone exists at %s", clone_path)

        return RepoHandle(url=url, slug=slug, fs_slug=fs_slug, clone_path=clone_path)

    def sync(self, handle: RepoHandle) -> None:
        """Fetch + fast-forward main branch (does not touch active worktrees)."""
        log.info("Syncing %s", handle.slug)
        subprocess.run(
            ["git", "-C", str(handle.clone_path), "fetch", "--all", "--prune"],
            check=True,
            env=self._git_env(),
        )
        # Re-inject substrate in case git touched anything (shouldn't, but safe)
        self._inject_substrate(handle.clone_path)

    # ------------------------------------------------------------------
    # Worktree lifecycle
    # ------------------------------------------------------------------

    def create_worktree(self, handle: RepoHandle, adw_id: str,
                        branch_name: str | None = None) -> Path:
        """Create a fresh worktree for an ADW run.

        Worktree path is <home>/trees/<fs_slug>__<adw_id>/. This lives OUTSIDE
        the clone's tree/ dir so multiple concurrent runs don't collide with
        the ADW's own tree/ directory expectations (tac-7 puts worktrees at
        <clone>/trees/<adw_id>/ — we pre-create ours at tac-master's top-level
        trees/ and symlink them into place).
        """
        wt_name = f"{handle.fs_slug}__{adw_id}"
        wt_path = self.trees_dir / wt_name
        if wt_path.exists():
            log.info("Worktree already exists: %s", wt_path)
            return wt_path

        # Create from origin/main (or origin/master as fallback)
        base = self._default_branch(handle)
        branch = branch_name or f"tac/{adw_id}"

        log.info("Creating worktree %s from %s/%s", wt_path, handle.slug, base)
        subprocess.run(
            [
                "git", "-C", str(handle.clone_path),
                "worktree", "add", "-b", branch, str(wt_path), f"origin/{base}",
            ],
            check=True,
            env=self._git_env(),
        )

        # Inject substrate into the worktree too
        self._inject_substrate(wt_path)

        # ALSO symlink wt_path into the clone's tree/ directory so tac-7 ADWs
        # that expect <clone>/trees/<adw_id>/ find it.
        tac7_trees_dir = handle.clone_path / "trees"
        tac7_trees_dir.mkdir(exist_ok=True)
        tac7_symlink = tac7_trees_dir / adw_id
        if not tac7_symlink.exists():
            tac7_symlink.symlink_to(wt_path, target_is_directory=True)

        return wt_path

    def cleanup_worktree(self, handle: RepoHandle, adw_id: str,
                         delete_branch: bool = False) -> None:
        """Remove a worktree and optionally its feature branch."""
        wt_name = f"{handle.fs_slug}__{adw_id}"
        wt_path = self.trees_dir / wt_name

        # Remove tac-7-style symlink first
        tac7_symlink = handle.clone_path / "trees" / adw_id
        if tac7_symlink.is_symlink():
            tac7_symlink.unlink()

        if wt_path.exists():
            log.info("Removing worktree %s", wt_path)
            subprocess.run(
                ["git", "-C", str(handle.clone_path),
                 "worktree", "remove", "--force", str(wt_path)],
                check=False,
                env=self._git_env(),
            )
            if wt_path.exists():
                shutil.rmtree(wt_path, ignore_errors=True)

        if delete_branch:
            branch = f"tac/{adw_id}"
            subprocess.run(
                ["git", "-C", str(handle.clone_path), "branch", "-D", branch],
                check=False,
                env=self._git_env(),
            )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _authed_url(self, url: str) -> str:
        """Inject PAT into https://github.com/... URLs for clone/push."""
        pat = self.identity.get("GITHUB_PAT", "")
        user = self.identity.get("GITHUB_USER", "krypto-agent")
        if not pat or not url.startswith("https://"):
            return url
        return url.replace("https://", f"https://{user}:{pat}@")

    def _git_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.setdefault("GIT_TERMINAL_PROMPT", "0")
        return env

    def _configure_identity(self, clone_path: Path) -> None:
        """Set committer identity on the clone (local, not global)."""
        user = self.identity.get("GITHUB_USER", "krypto-agent")
        email = self.identity.get("GITHUB_EMAIL",
                                   f"{user}@users.noreply.github.com")
        subprocess.run(
            ["git", "-C", str(clone_path), "config", "user.name", user],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(clone_path), "config", "user.email", email],
            check=True,
        )

    def _inject_substrate(self, target: Path) -> None:
        """Symlink tac-master's adws/, .claude/, ai_docs/ into target.

        Also writes .git/info/exclude entries so they never appear in diffs.
        """
        for sub in SUBSTRATE_DIRS:
            src = self.home / sub
            dst = target / sub
            if not src.exists():
                log.warning("Substrate source missing: %s", src)
                continue
            if dst.exists() or dst.is_symlink():
                if dst.is_symlink() and os.readlink(dst) == str(src):
                    continue  # already linked correctly
                log.debug("Replacing existing %s", dst)
                if dst.is_symlink() or dst.is_file():
                    dst.unlink()
                else:
                    shutil.rmtree(dst)
            dst.symlink_to(src, target_is_directory=True)

        # Write exclude entries (idempotent). Target may be a clone or a worktree;
        # `git rev-parse --git-path info/exclude` resolves the correct location.
        try:
            exclude_path = subprocess.check_output(
                ["git", "-C", str(target), "rev-parse", "--git-path", "info/exclude"],
                text=True,
            ).strip()
            exclude_file = target / exclude_path if not os.path.isabs(exclude_path) else Path(exclude_path)
            exclude_file.parent.mkdir(parents=True, exist_ok=True)
            existing = exclude_file.read_text() if exclude_file.exists() else ""
            to_add = [e for e in RUNTIME_EXCLUDES if e not in existing]
            if to_add:
                with exclude_file.open("a") as f:
                    if existing and not existing.endswith("\n"):
                        f.write("\n")
                    f.write("# tac-master runtime excludes\n")
                    for e in to_add:
                        f.write(f"{e}\n")
        except subprocess.CalledProcessError as e:
            log.warning("Could not write git exclude for %s: %s", target, e)

    def _default_branch(self, handle: RepoHandle) -> str:
        """Detect origin default branch (main vs master)."""
        try:
            out = subprocess.check_output(
                ["git", "-C", str(handle.clone_path),
                 "symbolic-ref", "refs/remotes/origin/HEAD"],
                text=True,
            ).strip()
            # refs/remotes/origin/main -> main
            return out.rsplit("/", 1)[-1]
        except subprocess.CalledProcessError:
            return "main"
