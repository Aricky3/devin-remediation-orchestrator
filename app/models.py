"""Domain models and the derived remediation state machine."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskState(str, Enum):
    """Orchestrator-derived lifecycle of a single remediation task.

    This is intentionally a small, stable vocabulary that is meaningful to an
    engineering leader, derived from the noisier raw Devin session status.
    """

    PENDING = "pending"        # issue detected, not yet dispatched to Devin
    DISPATCHED = "dispatched"  # Devin session created, not yet running
    RUNNING = "running"        # Devin actively working
    BLOCKED = "blocked"        # Devin waiting for human input
    PR_OPEN = "pr_open"        # a pull request exists, session still active
    COMPLETED = "completed"    # terminal + produced a PR (success)
    FAILED = "failed"          # terminal without a PR (failure)

    @property
    def is_terminal(self) -> bool:
        return self in (TaskState.COMPLETED, TaskState.FAILED)

    @property
    def is_active(self) -> bool:
        return self in (
            TaskState.DISPATCHED,
            TaskState.RUNNING,
            TaskState.BLOCKED,
            TaskState.PR_OPEN,
        )


# Raw Devin v3 session `status` values that mean the session has ended for good.
# Observed v3 statuses: new, claimed, running, suspended, resuming, exit, error.
# Only exit/error are truly terminal; `suspended` can resume, so it maps to BLOCKED.
DEVIN_TERMINAL_STATUSES = {
    "exit",
    "exited",
    "error",
    "cancelled",
    "canceled",
}

# status_detail values that indicate the session needs a human before it can progress.
DEVIN_BLOCKED_DETAILS = {"waiting_for_user", "waiting_for_approval", "blocked"}


def derive_state(
    *,
    status: str | None,
    status_detail: str | None,
    pull_requests: list | None,
    structured_output: dict | None,
) -> TaskState:
    """Map a raw Devin v3 session response onto our derived TaskState."""
    status = (status or "").lower()
    status_detail = (status_detail or "").lower()
    pull_requests = pull_requests or []
    so = structured_output or {}
    so_status = str(so.get("status", "")).lower()

    has_pr = bool(pull_requests) or bool(so.get("pr_url"))
    terminal = (
        status in DEVIN_TERMINAL_STATUSES
        or status_detail == "finished"
        or so_status in {"completed", "failed"}
    )

    if terminal:
        if has_pr or so_status == "completed":
            return TaskState.COMPLETED
        return TaskState.FAILED

    # Not terminal below this point. A PR already opened is the strongest signal,
    # so surface PR_OPEN even if Devin is now idling and waiting on the user.
    if has_pr:
        return TaskState.PR_OPEN
    if status_detail in DEVIN_BLOCKED_DETAILS or status in ("blocked", "suspended"):
        return TaskState.BLOCKED
    if status in ("", "new", "pending", "claimed"):
        return TaskState.DISPATCHED
    return TaskState.RUNNING


@dataclass
class Issue:
    """A GitHub issue eligible for remediation."""

    number: int
    title: str
    body: str
    url: str
    labels: list[str] = field(default_factory=list)


@dataclass
class Task:
    """A remediation task tracked by the orchestrator (one per issue)."""

    repo: str
    issue_number: int
    issue_title: str
    issue_url: str
    state: TaskState = TaskState.PENDING
    session_id: str | None = None
    session_url: str | None = None
    devin_status: str | None = None
    devin_status_detail: str | None = None
    pr_url: str | None = None
    acus_consumed: float = 0.0
    structured_output: dict[str, Any] | None = None
    error: str | None = None
    created_at: str | None = None
    dispatched_at: str | None = None
    pr_opened_at: str | None = None
    completed_at: str | None = None
    last_polled_at: str | None = None
    id: int | None = None
