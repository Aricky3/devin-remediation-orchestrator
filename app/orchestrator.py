"""Core orchestration logic: issue -> Devin session -> PR, plus reconciliation.

This module is deliberately framework-agnostic so it can be unit-tested without
FastAPI or a live network. It coordinates the DB, the Devin v3 client, and the
GitHub client.
"""

from __future__ import annotations

import logging
import statistics
from datetime import UTC, datetime

from .config import Settings
from .db import Database, now_iso
from .devin_client import REMEDIATION_OUTPUT_SCHEMA, DevinError
from .github_client import GitHubError
from .logging_config import log_event
from .models import Issue, Task, TaskState, derive_state

logger = logging.getLogger("orchestrator")


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _extract_pr_url(session: dict) -> str | None:
    prs = session.get("pull_requests") or []
    if prs:
        first = prs[0]
        if isinstance(first, dict):
            # v3 returns {"pr_url": ..., "pr_state": ...}; tolerate "url"/"html_url" too.
            url = first.get("pr_url") or first.get("url") or first.get("html_url")
            if url:
                return url
        elif first:
            return str(first)
    so = session.get("structured_output") or {}
    return so.get("pr_url")


class Orchestrator:
    def __init__(self, settings: Settings, db: Database, devin, github):
        self.settings = settings
        self.db = db
        self.devin = devin
        self.github = github

    # -- prompt ----------------------------------------------------------------
    def build_prompt(self, issue: Issue) -> str:
        s = self.settings
        return (
            f"You are remediating a GitHub issue in the repository **{s.github_repo}** "
            f"({s.repo_url}).\n\n"
            f"## Issue #{issue.number}: {issue.title}\n\n"
            f"{issue.body}\n\n"
            "## Your task\n"
            "1. Investigate the issue in the repository and implement a **minimal, focused** fix.\n"
            "2. Do not make unrelated changes or broad refactors.\n"
            "3. Follow the repo's existing conventions; run any obvious local checks for the files you touch.\n"
            "4. **Add a regression test** that locks in the fix (test-driven): write or extend a unit "
            "test that fails on the old behavior and passes with your fix, so a future change that "
            "reverts it gets caught. Place it alongside the repo's existing tests and match their style.\n"
            "5. **Run the tests and capture the results.** Run your new unit test plus the existing tests "
            "covering the files you touched. If the repo has an integration or end-to-end suite that can "
            "realistically run in this environment, attempt it too. Be honest: if some suite cannot run "
            "here (e.g. it needs services you can't start), say so explicitly rather than skipping silently.\n"
            f"6. Open a pull request against the default branch of `{s.github_repo}` whose description "
            f"includes \"Closes #{issue.number}\" so merging the PR closes this issue.\n"
            "7. In the PR description, include a **'## Test results'** section with the exact commands you "
            "ran and their output (pass/fail), plus a one-line note on anything you couldn't run and why.\n\n"
            "When you are finished, set your structured output with status=completed, the pr_url, "
            "tests_added=true if you added a test, and test_results summarizing what you ran. "
            "If you cannot proceed, set status=blocked or failed and explain why in the summary."
        )

    # -- dispatch --------------------------------------------------------------
    def dispatch_issue(self, issue: Issue) -> Task:
        """Create (or return existing) a remediation task + Devin session for an issue."""
        existing = self.db.get_task_by_issue(self.settings.github_repo, issue.number)
        if existing and existing.session_id:
            return existing

        task = existing or Task(
            repo=self.settings.github_repo,
            issue_number=issue.number,
            issue_title=issue.title,
            issue_url=issue.url,
            state=TaskState.PENDING,
        )
        if task.id is None:
            task = self.db.create_task(task)

        prompt = self.build_prompt(issue)
        try:
            resp = self.devin.create_session(
                prompt,
                title=f"Remediate #{issue.number}: {issue.title}"[:120],
                tags=["auto-remediation", f"repo:{self.settings.github_repo}", f"issue:{issue.number}"],
                max_acu_limit=self.settings.devin_max_acu_limit,
                structured_output_schema=REMEDIATION_OUTPUT_SCHEMA,
                idempotent=True,
            )
        except DevinError as exc:
            task.state = TaskState.FAILED
            task.error = str(exc)
            self.db.update_task(task)
            self.db.add_event(task.id, "error", f"create_session failed: {exc}")
            log_event(logger, logging.ERROR, "dispatch_failed", issue=issue.number, error=str(exc))
            return task

        task.session_id = resp.get("session_id")
        task.session_url = resp.get("url")
        task.devin_status = resp.get("status")
        task.state = TaskState.DISPATCHED
        task.dispatched_at = now_iso()
        self.db.update_task(task)
        self.db.add_event(task.id, "dispatched", f"session {task.session_id}")
        log_event(
            logger, logging.INFO, "dispatched",
            issue=issue.number, session_id=task.session_id, session_url=task.session_url,
        )
        self._notify_dispatched(task)
        return task

    def scan_and_dispatch(self) -> list[Task]:
        """Find open labeled issues with no session yet and dispatch them (rate-limited)."""
        s = self.settings
        try:
            issues = self.github.list_open_issues_with_label(s.remediate_label)
        except GitHubError as exc:
            log_event(logger, logging.ERROR, "scan_failed", error=str(exc))
            return []

        active = len(self.db.list_active_tasks())
        budget = max(0, s.max_concurrent_sessions - active)
        dispatched: list[Task] = []
        for issue in issues:
            if budget <= 0:
                break
            existing = self.db.get_task_by_issue(s.github_repo, issue.number)
            if existing and (existing.session_id or existing.state.is_terminal):
                continue
            dispatched.append(self.dispatch_issue(issue))
            budget -= 1
        if dispatched:
            log_event(logger, logging.INFO, "scan_dispatched", count=len(dispatched))
        return dispatched

    # -- reconciliation --------------------------------------------------------
    def reconcile_task(self, task: Task) -> Task:
        if not task.session_id:
            return task
        try:
            session = self.devin.get_session(task.session_id)
        except (DevinError, KeyError) as exc:
            log_event(logger, logging.WARNING, "poll_failed", task=task.id, error=str(exc))
            return task

        prev_state = task.state
        task.devin_status = session.get("status")
        task.devin_status_detail = session.get("status_detail")
        task.acus_consumed = session.get("acus_consumed") or task.acus_consumed
        task.structured_output = session.get("structured_output") or task.structured_output
        pr_url = _extract_pr_url(session)
        if pr_url:
            task.pr_url = pr_url

        new_state = derive_state(
            status=task.devin_status,
            status_detail=task.devin_status_detail,
            pull_requests=session.get("pull_requests"),
            structured_output=task.structured_output,
        )
        task.state = new_state
        task.last_polled_at = now_iso()
        if pr_url and not task.pr_opened_at:
            task.pr_opened_at = now_iso()
        if new_state.is_terminal and not task.completed_at:
            task.completed_at = now_iso()
        self.db.update_task(task)

        if new_state != prev_state:
            self.db.add_event(task.id, f"state:{new_state.value}", task.devin_status or "")
            log_event(
                logger, logging.INFO, "state_change",
                task=task.id, issue=task.issue_number,
                old=prev_state.value, new=new_state.value, pr=task.pr_url,
            )
            self._notify_state_change(task, prev_state, new_state)
        return task

    def poll_active(self) -> int:
        tasks = self.db.list_active_tasks()
        for task in tasks:
            self.reconcile_task(task)
        return len(tasks)

    # -- GitHub status updates (observable outputs) ----------------------------
    def _safe_github(self, fn, *args, **kwargs) -> None:
        try:
            fn(*args, **kwargs)
        except GitHubError as exc:
            log_event(logger, logging.WARNING, "github_update_failed", error=str(exc))

    def _notify_dispatched(self, task: Task) -> None:
        self._safe_github(self.github.add_labels, task.issue_number, [self.settings.in_progress_label])
        self._safe_github(
            self.github.add_comment,
            task.issue_number,
            f"🤖 **Devin remediation started.**\n\nTracking session: {task.session_url}\n\n"
            "_Posted automatically by the Devin Remediation Orchestrator._",
        )

    def _notify_state_change(self, task: Task, prev: TaskState, new: TaskState) -> None:
        gh = self.github
        if new == TaskState.PR_OPEN and prev != TaskState.PR_OPEN:
            self._safe_github(
                gh.add_comment, task.issue_number,
                f"✅ **Devin opened a pull request:** {task.pr_url}",
            )
        elif new == TaskState.BLOCKED:
            self._safe_github(
                gh.add_comment, task.issue_number,
                f"⏸️ Devin is blocked and needs input. Session: {task.session_url}",
            )
        elif new == TaskState.COMPLETED:
            self._safe_github(gh.add_labels, task.issue_number, [self.settings.done_label])
            self._safe_github(gh.remove_label, task.issue_number, self.settings.in_progress_label)
            summary = (task.structured_output or {}).get("summary", "")
            self._safe_github(
                gh.add_comment, task.issue_number,
                f"🎉 **Remediation complete.** PR: {task.pr_url}\n\n{summary}",
            )
        elif new == TaskState.FAILED:
            self._safe_github(gh.remove_label, task.issue_number, self.settings.in_progress_label)
            self._safe_github(
                gh.add_comment, task.issue_number,
                f"❌ Devin could not remediate this issue automatically. Session: {task.session_url}",
            )

    # -- metrics ---------------------------------------------------------------
    def metrics(self) -> dict:
        tasks = self.db.list_tasks()
        by_state: dict[str, int] = {s.value: 0 for s in TaskState}
        for t in tasks:
            by_state[t.state.value] = by_state.get(t.state.value, 0) + 1

        completed = by_state.get(TaskState.COMPLETED.value, 0)
        failed = by_state.get(TaskState.FAILED.value, 0)
        finished = completed + failed
        success_rate = (completed / finished) if finished else None

        # Time-to-PR (minutes) over tasks that opened a PR.
        ttp: list[float] = []
        for t in tasks:
            start = _parse_iso(t.dispatched_at)
            pr = _parse_iso(t.pr_opened_at)
            if start and pr:
                ttp.append((pr - start).total_seconds() / 60.0)

        active = sum(1 for t in tasks if t.state.is_active)
        total_acus = round(sum(t.acus_consumed for t in tasks), 2)

        return {
            "repo": self.settings.github_repo,
            "generated_at": datetime.now(tz=UTC).isoformat(),
            "totals": {
                "tasks": len(tasks),
                "active": active,
                "completed": completed,
                "failed": failed,
            },
            "by_state": by_state,
            "success_rate": round(success_rate, 3) if success_rate is not None else None,
            "median_time_to_pr_minutes": round(statistics.median(ttp), 1) if ttp else None,
            "total_acus_consumed": total_acus,
        }
