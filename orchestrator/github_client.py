"""Minimal GitHub API client for tac-master.

Used by the dispatcher to poll issues across allowlisted repos. Does NOT
replace the adws/adw_modules/github.py module — that one runs INSIDE the
worktree context and uses `gh` CLI. This client runs in the orchestrator
process and uses the REST API directly with the krypto-agent PAT.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

API_BASE = "https://api.github.com"


@dataclass
class Issue:
    number: int
    title: str
    body: str
    state: str
    labels: list[str]
    comments_count: int
    updated_at: str
    html_url: str


@dataclass
class Comment:
    id: int
    body: str
    user: str
    created_at: str


class GitHubClient:
    def __init__(self, pat: str, user_agent: str = "tac-master/0.1"):
        self.client = httpx.Client(
            base_url=API_BASE,
            headers={
                "Authorization": f"Bearer {pat}",
                "Accept": "application/vnd.github+json",
                "User-Agent": user_agent,
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30.0,
        )

    def close(self) -> None:
        self.client.close()

    # ------------------------------------------------------------------
    @staticmethod
    def owner_repo(url: str) -> tuple[str, str]:
        """Extract (owner, repo) from https://github.com/owner/repo[.git]."""
        path = urlparse(url).path.strip("/").removesuffix(".git")
        owner, repo = path.split("/", 1)
        return owner, repo

    # ------------------------------------------------------------------
    def list_open_issues(self, repo_url: str,
                         labels: list[str] | None = None) -> list[Issue]:
        owner, repo = self.owner_repo(repo_url)
        params: dict[str, Any] = {"state": "open", "per_page": 100}
        if labels:
            params["labels"] = ",".join(labels)
        try:
            r = self.client.get(f"/repos/{owner}/{repo}/issues", params=params)
            r.raise_for_status()
        except httpx.HTTPError as e:
            log.error("Failed to list issues for %s: %s", repo_url, e)
            return []

        issues: list[Issue] = []
        for raw in r.json():
            # Skip PRs (GitHub returns them in /issues too)
            if "pull_request" in raw:
                continue
            issues.append(
                Issue(
                    number=raw["number"],
                    title=raw.get("title", ""),
                    body=raw.get("body") or "",
                    state=raw.get("state", "open"),
                    labels=[label["name"] for label in raw.get("labels", [])],
                    comments_count=raw.get("comments", 0),
                    updated_at=raw.get("updated_at", ""),
                    html_url=raw.get("html_url", ""),
                )
            )
        return issues

    def list_comments(self, repo_url: str, issue_number: int) -> list[Comment]:
        owner, repo = self.owner_repo(repo_url)
        try:
            r = self.client.get(
                f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
                params={"per_page": 100},
            )
            r.raise_for_status()
        except httpx.HTTPError as e:
            log.error("Failed to list comments for %s#%d: %s",
                      repo_url, issue_number, e)
            return []
        return [
            Comment(
                id=c["id"],
                body=c.get("body") or "",
                user=c.get("user", {}).get("login", ""),
                created_at=c.get("created_at", ""),
            )
            for c in r.json()
        ]

    def post_comment(self, repo_url: str, issue_number: int, body: str) -> bool:
        owner, repo = self.owner_repo(repo_url)
        try:
            r = self.client.post(
                f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
                json={"body": body},
            )
            r.raise_for_status()
            return True
        except httpx.HTTPError as e:
            log.error("Failed to post comment on %s#%d: %s",
                      repo_url, issue_number, e)
            return False
