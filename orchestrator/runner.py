"""Runtime abstraction for spawning ADW Lead processes.

Two implementations:

  NativeRunner  — subprocess.Popen from inside the clone (fast, shared host
                  dependencies). Default.

  PodmanRunner  — rootless `podman run` with bind-mounted worktree and
                  substrate. Each run gets a fresh container from a base
                  image (tac-worker:latest by default) or a per-repo image.
                  Slower dispatch (~2-5s per run) but supports heterogeneous
                  repo runtimes and provides an additional isolation layer.

The dispatcher selects per-repo via `RepoConfig.runtime`. Existing reap
logic (polling the spawned pid via os.kill(pid, 0)) works unchanged for
both runners because PodmanRunner spawns `podman run` in the foreground —
when the container exits, the podman client process exits, and the pid
is reaped normally.

Why not `podman run --detach`?
  Because then we'd need `podman wait`/`podman inspect` for reaping, which
  is a different code path. Keeping podman in the foreground gives us a
  unix pid to poll with the same reap logic as native.
"""

from __future__ import annotations

import logging
import os
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


# Env vars that get forwarded into containers. Everything else is filtered.
CONTAINER_ENV_ALLOWLIST = {
    "GITHUB_REPO_URL",
    "GITHUB_PAT",
    "ANTHROPIC_API_KEY",
    "ADW_MODEL_SET",
    "CLAUDE_CODE_PATH",
    "TAC_MASTER_HOME",
    "TAC_DASHBOARD_URL",
}


@dataclass
class RunSpec:
    """Everything a runner needs to spawn a Lead process."""
    adw_id: str
    repo_url: str
    issue_number: int
    workflow: str              # script filename e.g. "adw_sdlc_iso.py"
    clone_path: Path           # /srv/tac-master/repos/<slug>
    worktree_path: Path        # /srv/tac-master/trees/<slug>__<adw_id>
    env: dict[str, str]
    log_file: Path
    substrate_home: Path       # /srv/tac-master (for container bind mounts)
    container_image: str = "tac-worker:latest"
    extra_mounts: list[tuple[Path, str, str]] = field(default_factory=list)
    # (host_path, container_path, mode)  mode="ro"|"rw"


class Runner(ABC):
    """Abstract: spawn a detached process and return its host pid."""

    @abstractmethod
    def spawn(self, spec: RunSpec) -> int:
        ...

    def is_running(self, pid: int) -> bool:
        """Default: check via os.kill(pid, 0). Works for native and for
        foreground `podman run` invocations."""
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True


# ---------------------------------------------------------------------------
# Native runner (default)
# ---------------------------------------------------------------------------


class NativeRunner(Runner):
    """Spawn `uv run adws/<script>` from inside the clone directory.

    This is the tac-7 flow: the ADW script discovers its worktree via the
    pre-populated state file, runs from <clone>/adws/..., and inherits the
    filled env.
    """

    def spawn(self, spec: RunSpec) -> int:
        script_path = spec.clone_path / "adws" / spec.workflow
        if not script_path.exists():
            raise FileNotFoundError(f"ADW script missing: {script_path}")

        cmd = [
            "uv", "run", str(script_path),
            str(spec.issue_number), spec.adw_id,
        ]

        spec.log_file.parent.mkdir(parents=True, exist_ok=True)
        log_fh = spec.log_file.open("ab")
        proc = subprocess.Popen(
            cmd,
            cwd=str(spec.clone_path),
            env=spec.env,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        log.info(
            "NativeRunner: spawned pid=%d adw=%s repo=%s",
            proc.pid, spec.adw_id, spec.repo_url,
        )
        return proc.pid


# ---------------------------------------------------------------------------
# Podman runner (opt-in)
# ---------------------------------------------------------------------------


class PodmanRunner(Runner):
    """Wrap the ADW invocation in a rootless podman container.

    The container runs `podman run` in the FOREGROUND (not detached) so
    the dispatcher's pid-based reap logic still works. When the ADW inside
    the container exits, the podman client exits, and reap_finished_runs
    observes the dead pid.

    Mounts:
      worktree_path   → /workspace       (rw, where the ADW edits code)
      substrate/adws  → /workspace/adws  (ro, tac-7 ADW scripts)
      substrate/.claude → /workspace/.claude (ro, commands + hooks)

    Network: --network host so the .claude/hooks/send_event.py can still
    POST to the dashboard at localhost:4000. Host networking also keeps
    port allocation (9100-9114/9200-9214) working for any local service
    the ADW spins up during the review phase.
    """

    def __init__(self, default_image: str = "tac-worker:latest",
                 podman_path: str = "podman"):
        self.default_image = default_image
        self.podman_path = podman_path

    def spawn(self, spec: RunSpec) -> int:
        image = spec.container_image or self.default_image
        container_name = f"tac-{spec.adw_id}"

        # Build mount list
        mounts: list[str] = [
            "-v", f"{spec.worktree_path}:/workspace:Z,rw",
            "-v", f"{spec.substrate_home / 'adws'}:/workspace/adws:Z,ro",
            "-v", f"{spec.substrate_home / '.claude'}:/workspace/.claude:Z,ro",
        ]
        if (spec.substrate_home / "ai_docs").exists():
            mounts += ["-v", f"{spec.substrate_home / 'ai_docs'}:/workspace/ai_docs:Z,ro"]
        for host, container, mode in spec.extra_mounts:
            mounts += ["-v", f"{host}:{container}:Z,{mode}"]

        # Whitelist env vars into the container
        env_flags: list[str] = []
        for k, v in spec.env.items():
            if k in CONTAINER_ENV_ALLOWLIST and v:
                env_flags += ["--env", f"{k}={v}"]

        # Force container-internal home mount for claude code config
        env_flags += ["--env", "HOME=/home/worker"]

        cmd = [
            self.podman_path, "run",
            "--rm",
            "--name", container_name,
            "--network", "host",
            "--workdir", "/workspace",
            "--user", "worker",
            *mounts,
            *env_flags,
            image,
            # Command inside the container — matches the native cmd
            "uv", "run", f"adws/{spec.workflow}",
            str(spec.issue_number), spec.adw_id,
        ]

        spec.log_file.parent.mkdir(parents=True, exist_ok=True)
        log_fh = spec.log_file.open("ab")
        log_fh.write(
            f"# podman run {container_name} image={image}\n".encode()
        )
        log_fh.write(f"# cmd: {' '.join(cmd)}\n".encode())
        log_fh.flush()

        # Foreground podman run, detached session
        proc = subprocess.Popen(
            cmd,
            env=os.environ.copy(),  # podman itself needs PATH etc.
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        log.info(
            "PodmanRunner: spawned pid=%d container=%s adw=%s image=%s",
            proc.pid, container_name, spec.adw_id, image,
        )
        return proc.pid

    def kill_container(self, adw_id: str) -> None:
        """Best-effort kill of a running container by name."""
        name = f"tac-{adw_id}"
        try:
            subprocess.run(
                [self.podman_path, "kill", name],
                capture_output=True, check=False, timeout=10,
            )
            subprocess.run(
                [self.podman_path, "rm", "-f", name],
                capture_output=True, check=False, timeout=10,
            )
        except Exception as e:
            log.warning("kill_container(%s) failed: %s", adw_id, e)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_runner(kind: str, default_image: str = "tac-worker:latest") -> Runner:
    """Build a Runner for the given kind. Case-insensitive."""
    kind = (kind or "native").lower()
    if kind in ("native", "host", ""):
        return NativeRunner()
    if kind in ("podman", "container"):
        return PodmanRunner(default_image=default_image)
    raise ValueError(
        f"Unknown runtime: {kind!r}. Expected 'native' or 'podman'."
    )
