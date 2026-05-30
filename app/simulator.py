"""A drop-in stand-in for DevinClient used in dry-run / demo / CI mode.

It mimics the shape of the Devin v3 API responses and deterministically advances
a session from ``new`` -> ``running`` -> ``finished`` (with a fake PR) based on
elapsed wall-clock time, so the full orchestration loop can be exercised without
spending ACUs or needing network access.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

# Seconds (since dispatch) after which a simulated session opens a PR / finishes.
_PR_AFTER = 8
_FINISH_AFTER = 16


class SimulatedDevinClient:
    def __init__(self, repo: str = "Aricky3/superset", **_ignored):
        self.repo = repo
        self._sessions: dict[str, dict[str, Any]] = {}

    def whoami(self) -> dict:
        return {"principal_type": "service_user", "service_user_name": "Simulated", "org_id": "org-sim"}

    def create_session(self, prompt: str, *, title=None, tags=None, **_kw) -> dict:
        sid = uuid.uuid4().hex
        self._sessions[sid] = {"created": time.time(), "title": title, "tags": tags or []}
        return {
            "session_id": sid,
            "url": f"https://app.devin.ai/sessions/{sid}",
            "status": "new",
            "title": title,
            "tags": tags,
        }

    def get_session(self, session_id: str) -> dict:
        s = self._sessions.get(session_id)
        if not s:
            raise KeyError(session_id)
        elapsed = time.time() - s["created"]
        pr_num = (abs(hash(session_id)) % 9000) + 1000
        if elapsed >= _FINISH_AFTER:
            return {
                "session_id": session_id,
                "status": "finished",
                "status_detail": None,
                "acus_consumed": 3.2,
                "pull_requests": [{"url": f"https://github.com/{self.repo}/pull/{pr_num}"}],
                "structured_output": {
                    "status": "completed",
                    "pr_url": f"https://github.com/{self.repo}/pull/{pr_num}",
                    "summary": "Simulated remediation: applied a minimal, focused fix and opened a PR.",
                },
            }
        if elapsed >= _PR_AFTER:
            return {
                "session_id": session_id,
                "status": "running",
                "status_detail": "working",
                "acus_consumed": 1.8,
                "pull_requests": [{"url": f"https://github.com/{self.repo}/pull/{pr_num}"}],
                "structured_output": None,
            }
        return {
            "session_id": session_id,
            "status": "running",
            "status_detail": "working",
            "acus_consumed": 0.6,
            "pull_requests": [],
            "structured_output": None,
        }

    def list_sessions(self, limit: int = 50) -> dict:
        return {"sessions": list(self._sessions.keys())[:limit]}

    def close(self) -> None:  # parity with DevinClient
        pass
