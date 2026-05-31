"""Minimal GitHub REST client for the orchestrator.

Used to (a) discover issues labeled for remediation and (b) post observable
status updates back onto issues. Uses the REST API directly via httpx to avoid
a heavy dependency.
"""

from __future__ import annotations

import hashlib
import hmac

import httpx

from .models import Issue


class GitHubError(RuntimeError):
    pass


class GitHubClient:
    def __init__(
        self,
        token: str,
        repo: str,
        api_url: str = "https://api.github.com",
        timeout: float = 30.0,
    ):
        self.repo = repo
        self.api_url = api_url.rstrip("/")
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(timeout=timeout, headers=headers)

    def _request(self, method: str, path: str, **kwargs):
        url = f"{self.api_url}{path}"
        resp = self._client.request(method, url, **kwargs)
        if resp.status_code >= 400:
            raise GitHubError(f"GitHub {method} {path} -> {resp.status_code}: {resp.text[:500]}")
        return resp.json() if resp.content else {}

    def list_open_issues_with_label(self, label: str) -> list[Issue]:
        """Return open issues (excluding pull requests) carrying ``label``."""
        issues: list[Issue] = []
        page = 1
        while True:
            data = self._request(
                "GET",
                f"/repos/{self.repo}/issues",
                params={"state": "open", "labels": label, "per_page": 100, "page": page},
            )
            if not data:
                break
            for it in data:
                # The issues endpoint also returns PRs; skip those.
                if "pull_request" in it:
                    continue
                issues.append(
                    Issue(
                        number=it["number"],
                        title=it["title"],
                        body=it.get("body") or "",
                        url=it["html_url"],
                        labels=[lbl["name"] for lbl in it.get("labels", [])],
                    )
                )
            if len(data) < 100:
                break
            page += 1
        return issues

    def create_issue(self, title: str, body: str, labels: list[str] | None = None) -> dict:
        return self._request(
            "POST",
            f"/repos/{self.repo}/issues",
            json={"title": title, "body": body, "labels": labels or []},
        )

    def find_open_issue_by_title(self, title: str) -> dict | None:
        page = 1
        while True:
            data = self._request(
                "GET",
                f"/repos/{self.repo}/issues",
                params={"state": "open", "per_page": 100, "page": page},
            )
            if not data:
                return None
            for it in data:
                if "pull_request" in it:
                    continue
                if it.get("title") == title:
                    return it
            if len(data) < 100:
                return None
            page += 1

    def add_comment(self, issue_number: int, body: str) -> None:
        self._request(
            "POST", f"/repos/{self.repo}/issues/{issue_number}/comments", json={"body": body}
        )

    def add_labels(self, issue_number: int, labels: list[str]) -> None:
        self._request(
            "POST", f"/repos/{self.repo}/issues/{issue_number}/labels", json={"labels": labels}
        )

    def remove_label(self, issue_number: int, label: str) -> None:
        try:
            self._request(
                "DELETE", f"/repos/{self.repo}/issues/{issue_number}/labels/{label}"
            )
        except GitHubError:
            # Label may not be present; ignore.
            pass

    def ensure_label_exists(self, name: str, color: str = "5319e7", description: str = "") -> None:
        try:
            self._request(
                "POST",
                f"/repos/{self.repo}/labels",
                json={"name": name, "color": color, "description": description},
            )
        except GitHubError:
            # 422 if it already exists.
            pass

    def close(self) -> None:
        self._client.close()


def verify_webhook_signature(secret: str, signature_header: str | None, body: bytes) -> bool:
    """Verify a GitHub ``X-Hub-Signature-256`` header against the raw body."""
    if not secret:
        # No secret configured -> signature verification disabled.
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)
