"""FastAPI application: event endpoints, background scheduler, and dashboard."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .config import get_settings
from .db import Database
from .devin_client import DevinClient
from .github_client import GitHubClient, verify_webhook_signature
from .logging_config import configure_logging, log_event
from .models import Issue, Task
from .orchestrator import Orchestrator
from .simulator import SimulatedDevinClient

logger = logging.getLogger("main")
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def task_to_dict(task: Task) -> dict:
    return {
        "id": task.id,
        "issue_number": task.issue_number,
        "issue_title": task.issue_title,
        "issue_url": task.issue_url,
        "state": task.state.value,
        "session_id": task.session_id,
        "session_url": task.session_url,
        "devin_status": task.devin_status,
        "devin_status_detail": task.devin_status_detail,
        "pr_url": task.pr_url,
        "acus_consumed": task.acus_consumed,
        "summary": (task.structured_output or {}).get("summary"),
        "error": task.error,
        "created_at": task.created_at,
        "dispatched_at": task.dispatched_at,
        "pr_opened_at": task.pr_opened_at,
        "completed_at": task.completed_at,
        "last_polled_at": task.last_polled_at,
    }


def build_orchestrator(settings) -> Orchestrator:
    db = Database(settings.database_path)
    if settings.dry_run:
        devin = SimulatedDevinClient(repo=settings.github_repo)
    else:
        devin = DevinClient(
            api_key=settings.devin_api_key,
            org_id=settings.devin_org_id,
            base_url=settings.devin_base_url,
        )
    github = GitHubClient(
        token=settings.github_token,
        repo=settings.github_repo,
        api_url=settings.github_api_url,
    )
    return Orchestrator(settings, db, devin, github)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    orch = build_orchestrator(settings)
    app.state.settings = settings
    app.state.orch = orch

    # Best-effort: make sure workflow labels exist on the repo.
    if not settings.dry_run and settings.github_token:
        for name, color, desc in [
            (settings.remediate_label, "5319e7", "Queue for autonomous Devin remediation"),
            (settings.in_progress_label, "fbca04", "Devin is actively remediating"),
            (settings.done_label, "0e8a16", "Remediated by Devin"),
        ]:
            try:
                orch.github.ensure_label_exists(name, color, desc)
            except Exception:  # noqa: BLE001 - never block startup on label setup
                pass

    scheduler = BackgroundScheduler(daemon=True)
    if settings.scanner_enabled:
        scheduler.add_job(
            orch.scan_and_dispatch, "interval",
            seconds=settings.scan_interval_seconds, id="scanner",
            next_run_time=None,
        )
    scheduler.add_job(
        orch.poll_active, "interval",
        seconds=settings.poll_interval_seconds, id="poller",
    )
    scheduler.start()
    app.state.scheduler = scheduler
    log_event(
        logger, logging.INFO, "startup",
        repo=settings.github_repo, dry_run=settings.dry_run,
        scanner_enabled=settings.scanner_enabled,
    )
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        orch.devin.close()
        orch.github.close()
        orch.db.close()


app = FastAPI(title="Devin Remediation Orchestrator", version="0.1.0", lifespan=lifespan)


# --- health & observability ---------------------------------------------------
@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return app.state.orch.metrics()


@app.get("/api/tasks")
def list_tasks():
    return [task_to_dict(t) for t in app.state.orch.db.list_tasks()]


@app.get("/api/tasks/{task_id}")
def get_task(task_id: int):
    task = app.state.orch.db.get_task(task_id)
    if not task:
        raise HTTPException(404, "task not found")
    return {"task": task_to_dict(task), "events": app.state.orch.db.list_events(task_id)}


# --- event ingress -------------------------------------------------------------
@app.post("/webhooks/github")
async def github_webhook(
    request: Request,
    x_github_event: str = Header(default=""),
    x_hub_signature_256: str = Header(default=None),
):
    settings = app.state.settings
    body = await request.body()
    if not verify_webhook_signature(settings.github_webhook_secret, x_hub_signature_256, body):
        raise HTTPException(401, "invalid signature")

    payload = await request.json()
    if x_github_event != "issues":
        return {"ignored": f"event {x_github_event}"}

    action = payload.get("action")
    issue = payload.get("issue", {})
    labels = [lbl.get("name") for lbl in issue.get("labels", [])]
    if action not in ("opened", "labeled", "reopened"):
        return {"ignored": f"action {action}"}
    if settings.remediate_label not in labels:
        return {"ignored": "missing remediate label"}

    issue_obj = Issue(
        number=issue["number"],
        title=issue.get("title", ""),
        body=issue.get("body") or "",
        url=issue.get("html_url", ""),
        labels=labels,
    )
    task = app.state.orch.dispatch_issue(issue_obj)
    return {"dispatched": True, "task": task_to_dict(task)}


@app.post("/api/dispatch/{issue_number}")
def manual_dispatch(issue_number: int):
    """Manually trigger remediation for an issue number (fetches it from GitHub)."""
    orch = app.state.orch
    issues = orch.github.list_open_issues_with_label(orch.settings.remediate_label)
    match = next((i for i in issues if i.number == issue_number), None)
    if not match:
        raise HTTPException(404, f"issue #{issue_number} not found with remediate label")
    return {"task": task_to_dict(orch.dispatch_issue(match))}


@app.post("/api/simulate/issue")
def simulate_issue(issue: dict):
    """Inject a synthetic issue (used for demos / CI in dry-run mode)."""
    issue_obj = Issue(
        number=int(issue["number"]),
        title=issue.get("title", "Synthetic issue"),
        body=issue.get("body", ""),
        url=issue.get("url", f"{app.state.settings.repo_url}/issues/{issue['number']}"),
        labels=[app.state.settings.remediate_label],
    )
    return {"task": task_to_dict(app.state.orch.dispatch_issue(issue_obj))}


# --- dashboard -----------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return TEMPLATES.TemplateResponse(request, "dashboard.html", {"request": request})
