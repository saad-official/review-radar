"""Create a GitHub issue from an approved issue proposal (REST, `POST /repos/{o}/{r}/issues`).

Token resolution: the app's own token (decrypted with its HKDF-derived key) first, then the
`GITHUB_TOKEN` environment fallback. A fine-grained PAT needs only "Issues: read and write"
on the one repository. The token is never logged, stored in plaintext, or returned.
"""

from __future__ import annotations

import re
from typing import Any

import httpx
from pydantic import BaseModel

REPO = re.compile(r"^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$")


class GitHubIssueError(Exception):
    def __init__(self, code: str, message: str, status: int = 502):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class CreatedIssue(BaseModel):
    number: int
    url: str
    api_url: str


class GitHubIssues:
    def __init__(
        self,
        token: str,
        *,
        client: httpx.Client | None = None,
        base_url: str = "https://api.github.com",
    ):
        if not token:
            raise GitHubIssueError("no_github_token", "no GitHub token is configured", 422)
        self._token = token
        self._client = client
        self._base = base_url.rstrip("/")

    def create_issue(
        self, repo: str, title: str, body: str, labels: list[str] | None = None
    ) -> CreatedIssue:
        if not REPO.match(repo or ""):
            raise GitHubIssueError("bad_repo", f"{repo!r} is not an owner/name repository", 422)
        http = self._client or httpx.Client(timeout=20.0)
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "review-radar",
        }
        payload: dict[str, Any] = {"title": title[:256], "body": body[:65000]}
        if labels:
            payload["labels"] = labels
        try:
            response = http.post(f"{self._base}/repos/{repo}/issues", headers=headers, json=payload)
            if response.status_code == 422 and labels:
                # Labels that do not exist (and cannot be created by an issues-only token)
                # fail the whole request; the issue matters more than the label.
                payload.pop("labels")
                response = http.post(
                    f"{self._base}/repos/{repo}/issues", headers=headers, json=payload
                )
        except httpx.HTTPError as exc:
            raise GitHubIssueError("github_unreachable", f"could not reach GitHub: {exc}") from exc
        finally:
            if self._client is None:
                http.close()
        if response.status_code in (401, 403):
            raise GitHubIssueError(
                "github_forbidden",
                f"GitHub refused the token ({response.status_code}); it needs Issues: write "
                f"on {repo}",
                502,
            )
        if response.status_code == 404:
            raise GitHubIssueError(
                "github_repo_not_found", f"{repo} not found or not visible to the token", 502
            )
        if response.status_code == 410:
            raise GitHubIssueError("github_issues_disabled", f"issues are disabled on {repo}", 502)
        if response.status_code >= 300:
            raise GitHubIssueError(
                "github_error", f"GitHub answered {response.status_code}: {response.text[:200]}"
            )
        data = response.json()
        return CreatedIssue(number=data["number"], url=data["html_url"], api_url=data["url"])
