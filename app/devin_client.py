"""Client for the Devin API (v3 organization sessions).

Docs: https://docs.devin.ai/api-reference/overview
Base: https://api.devin.ai/v3
Auth: service-user token (``cog_`` prefix) via ``Authorization: Bearer``.

Endpoints used:
  POST /v3/organizations/{org_id}/sessions               create a session
  GET  /v3/organizations/{org_id}/sessions/{session_id}  retrieve session detail
  GET  /v3/organizations/{org_id}/sessions               list sessions
  GET  /v3/self                                           verify credentials
"""

from __future__ import annotations

from typing import Any

import httpx


class DevinError(RuntimeError):
    pass


# JSON schema we ask Devin to emit so the orchestrator gets a clean,
# machine-readable verdict for each remediation rather than parsing prose.
REMEDIATION_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": ["completed", "failed", "blocked"],
            "description": "completed if a PR was opened that fixes the issue",
        },
        "pr_url": {"type": "string", "description": "URL of the opened pull request"},
        "summary": {"type": "string", "description": "1-3 sentence summary of the fix"},
        "root_cause": {"type": "string"},
    },
    "required": ["status", "summary"],
}


class DevinClient:
    def __init__(
        self,
        api_key: str,
        org_id: str,
        base_url: str = "https://api.devin.ai/v3",
        timeout: float = 30.0,
    ):
        if not api_key:
            raise DevinError("DEVIN_API_KEY is not set")
        if not org_id:
            raise DevinError("DEVIN_ORG_ID is not set")
        self.org_id = org_id
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    # -- low level -------------------------------------------------------------
    def _org_path(self, *parts: str) -> str:
        return "/".join([self.base_url, "organizations", self.org_id, *parts])

    def _request(self, method: str, url: str, **kwargs) -> dict:
        try:
            resp = self._client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:  # network-level failure
            raise DevinError(f"Devin API request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise DevinError(
                f"Devin API {method} {url} -> {resp.status_code}: {resp.text[:500]}"
            )
        return resp.json() if resp.content else {}

    # -- public API ------------------------------------------------------------
    def whoami(self) -> dict:
        """Verify credentials and return service-user / org info."""
        return self._request("GET", f"{self.base_url}/self")

    def create_session(
        self,
        prompt: str,
        *,
        title: str | None = None,
        tags: list[str] | None = None,
        max_acu_limit: int | None = None,
        idempotent: bool = True,
        structured_output_schema: dict | None = None,
    ) -> dict:
        """Create a Devin session. Returns the create response (session_id, url, ...)."""
        body: dict[str, Any] = {"prompt": prompt, "idempotent": idempotent}
        if title:
            body["title"] = title
        if tags:
            body["tags"] = tags
        if max_acu_limit:
            body["max_acu_limit"] = max_acu_limit
        if structured_output_schema:
            body["structured_output_schema"] = structured_output_schema
        return self._request("POST", self._org_path("sessions"), json=body)

    def get_session(self, session_id: str) -> dict:
        """Retrieve full detail for a session (status, pull_requests, etc.)."""
        return self._request("GET", self._org_path("sessions", session_id))

    def list_sessions(self, limit: int = 50) -> dict:
        return self._request(
            "GET", self._org_path("sessions"), params={"limit": limit}
        )

    def close(self) -> None:
        self._client.close()
