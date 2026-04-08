"""tac-master orchestrator package.

Long-running daemon that polls GitHub issues across allowlisted repos,
dispatches work to Lead agents (adw_plan_iso.py), which spawn Workers
(phase ADWs) for Plan→Implement→Test→Evaluate→Release (PITER).
"""

__version__ = "0.1.0"
