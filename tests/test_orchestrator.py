
import pytest

from app.config import Settings
from app.db import Database
from app.models import Issue, TaskState
from app.orchestrator import Orchestrator


class FakeDevin:
    """Scriptable Devin stand-in: get_session returns queued responses in order."""

    def __init__(self):
        self.created = []
        self._responses = {}
        self._counter = 0

    def create_session(self, prompt, **kwargs):
        self._counter += 1
        sid = f"sess-{self._counter}"
        self.created.append({"prompt": prompt, "sid": sid, "kwargs": kwargs})
        return {"session_id": sid, "url": f"https://app.devin.ai/sessions/{sid}", "status": "new"}

    def queue(self, sid, responses):
        self._responses[sid] = list(responses)

    def get_session(self, session_id):
        resps = self._responses.get(session_id)
        if not resps:
            return {"session_id": session_id, "status": "running", "pull_requests": []}
        # Advance through queued responses; keep returning the last one when exhausted.
        return resps.pop(0) if len(resps) > 1 else resps[0]

    def close(self):
        pass


class FakeGitHub:
    def __init__(self):
        self.comments = []
        self.labels_added = []
        self.labels_removed = []
        self.issues = []

    def list_open_issues_with_label(self, label):
        return self.issues

    def add_comment(self, issue_number, body):
        self.comments.append((issue_number, body))

    def add_labels(self, issue_number, labels):
        self.labels_added.append((issue_number, labels))

    def remove_label(self, issue_number, label):
        self.labels_removed.append((issue_number, label))

    def ensure_label_exists(self, *a, **k):
        pass

    def close(self):
        pass


@pytest.fixture
def setup(tmp_path):
    settings = Settings(
        devin_api_key="x", devin_org_id="org-1", github_token="gh",
        github_repo="Aricky3/superset", database_path=str(tmp_path / "t.db"),
    )
    db = Database(settings.database_path)
    devin = FakeDevin()
    github = FakeGitHub()
    orch = Orchestrator(settings, db, devin, github)
    return orch, devin, github, db


def _issue(n=42):
    return Issue(number=n, title="Add timeouts", body="fix it", url=f"https://github.com/Aricky3/superset/issues/{n}")


def test_dispatch_creates_session_and_notifies(setup):
    orch, devin, github, db = setup
    task = orch.dispatch_issue(_issue())
    assert task.state == TaskState.DISPATCHED
    assert task.session_id == "sess-1"
    assert len(devin.created) == 1
    # In-progress label + a "started" comment were posted.
    assert github.labels_added and github.labels_added[0][1] == ["devin-in-progress"]
    assert any("started" in c[1] for c in github.comments)
    # Prompt references the repo and a "Closes #" instruction.
    prompt = devin.created[0]["prompt"]
    assert "Aricky3/superset" in prompt and "Closes #42" in prompt


def test_dispatch_is_idempotent(setup):
    orch, devin, github, db = setup
    orch.dispatch_issue(_issue())
    orch.dispatch_issue(_issue())  # same issue again
    assert len(devin.created) == 1


def test_full_lifecycle_to_completed(setup):
    orch, devin, github, db = setup
    task = orch.dispatch_issue(_issue())
    devin.queue(task.session_id, [
        {"status": "running", "pull_requests": []},
        {"status": "running", "pull_requests": [{"url": "https://github.com/Aricky3/superset/pull/7"}]},
        {"status": "finished", "pull_requests": [{"url": "https://github.com/Aricky3/superset/pull/7"}],
         "structured_output": {"status": "completed", "summary": "done", "pr_url": "https://github.com/Aricky3/superset/pull/7"},
         "acus_consumed": 4.0},
    ])
    orch.reconcile_task(db.get_task(task.id))  # running
    assert db.get_task(task.id).state == TaskState.RUNNING
    orch.reconcile_task(db.get_task(task.id))  # pr open
    t = db.get_task(task.id)
    assert t.state == TaskState.PR_OPEN
    assert t.pr_url.endswith("/pull/7")
    orch.reconcile_task(db.get_task(task.id))  # completed
    t = db.get_task(task.id)
    assert t.state == TaskState.COMPLETED
    assert t.completed_at and t.pr_opened_at
    assert ("devin-remediated" in lab for _, lab in github.labels_added)
    # PR-open + completion comments were posted.
    assert any("pull request" in c[1].lower() for c in github.comments)


def test_metrics_summary(setup):
    orch, devin, github, db = setup
    task = orch.dispatch_issue(_issue(1))
    devin.queue(task.session_id, [
        {"status": "finished", "pull_requests": [{"url": "https://github.com/Aricky3/superset/pull/1"}],
         "structured_output": {"status": "completed", "summary": "ok"}, "acus_consumed": 2.0},
    ])
    orch.reconcile_task(db.get_task(task.id))

    task2 = orch.dispatch_issue(_issue(2))
    devin.queue(task2.session_id, [{"status": "expired", "pull_requests": []}])
    orch.reconcile_task(db.get_task(task2.id))

    m = orch.metrics()
    assert m["totals"]["completed"] == 1
    assert m["totals"]["failed"] == 1
    assert m["success_rate"] == 0.5
    assert m["total_acus_consumed"] == 2.0
