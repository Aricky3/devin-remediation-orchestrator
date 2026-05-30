"""Thin SQLite persistence layer for tasks and their status timeline.

Kept dependency-free (stdlib sqlite3) and thread-safe with a single write lock,
which is sufficient for the orchestrator's modest write volume.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import UTC, datetime

from .models import Task, TaskState


def now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo TEXT NOT NULL,
    issue_number INTEGER NOT NULL,
    issue_title TEXT NOT NULL,
    issue_url TEXT NOT NULL,
    state TEXT NOT NULL,
    session_id TEXT,
    session_url TEXT,
    devin_status TEXT,
    devin_status_detail TEXT,
    pr_url TEXT,
    acus_consumed REAL DEFAULT 0,
    structured_output TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    dispatched_at TEXT,
    pr_opened_at TEXT,
    completed_at TEXT,
    last_polled_at TEXT,
    UNIQUE (repo, issue_number)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT,
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        if os.path.dirname(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # -- serialization helpers -------------------------------------------------
    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> Task:
        so = row["structured_output"]
        return Task(
            id=row["id"],
            repo=row["repo"],
            issue_number=row["issue_number"],
            issue_title=row["issue_title"],
            issue_url=row["issue_url"],
            state=TaskState(row["state"]),
            session_id=row["session_id"],
            session_url=row["session_url"],
            devin_status=row["devin_status"],
            devin_status_detail=row["devin_status_detail"],
            pr_url=row["pr_url"],
            acus_consumed=row["acus_consumed"] or 0.0,
            structured_output=json.loads(so) if so else None,
            error=row["error"],
            created_at=row["created_at"],
            dispatched_at=row["dispatched_at"],
            pr_opened_at=row["pr_opened_at"],
            completed_at=row["completed_at"],
            last_polled_at=row["last_polled_at"],
        )

    # -- task operations -------------------------------------------------------
    def get_task_by_issue(self, repo: str, issue_number: int) -> Task | None:
        cur = self._conn.execute(
            "SELECT * FROM tasks WHERE repo=? AND issue_number=?",
            (repo, issue_number),
        )
        row = cur.fetchone()
        return self._row_to_task(row) if row else None

    def get_task(self, task_id: int) -> Task | None:
        cur = self._conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,))
        row = cur.fetchone()
        return self._row_to_task(row) if row else None

    def list_tasks(self) -> list[Task]:
        cur = self._conn.execute("SELECT * FROM tasks ORDER BY created_at DESC")
        return [self._row_to_task(r) for r in cur.fetchall()]

    def list_active_tasks(self) -> list[Task]:
        active = (
            TaskState.DISPATCHED.value,
            TaskState.RUNNING.value,
            TaskState.BLOCKED.value,
            TaskState.PR_OPEN.value,
        )
        placeholders = ",".join("?" * len(active))
        cur = self._conn.execute(
            f"SELECT * FROM tasks WHERE state IN ({placeholders})", active
        )
        return [self._row_to_task(r) for r in cur.fetchall()]

    def create_task(self, task: Task) -> Task:
        with self._lock:
            task.created_at = task.created_at or now_iso()
            cur = self._conn.execute(
                """INSERT INTO tasks
                   (repo, issue_number, issue_title, issue_url, state, created_at)
                   VALUES (?,?,?,?,?,?)""",
                (
                    task.repo, task.issue_number, task.issue_title,
                    task.issue_url, task.state.value, task.created_at,
                ),
            )
            task.id = cur.lastrowid
            self._conn.commit()
        self.add_event(task.id, "created", f"issue #{task.issue_number} tracked")
        return task

    def update_task(self, task: Task) -> None:
        with self._lock:
            self._conn.execute(
                """UPDATE tasks SET
                    state=?, session_id=?, session_url=?, devin_status=?,
                    devin_status_detail=?, pr_url=?, acus_consumed=?,
                    structured_output=?, error=?, dispatched_at=?, pr_opened_at=?,
                    completed_at=?, last_polled_at=?
                   WHERE id=?""",
                (
                    task.state.value, task.session_id, task.session_url,
                    task.devin_status, task.devin_status_detail, task.pr_url,
                    task.acus_consumed,
                    json.dumps(task.structured_output) if task.structured_output else None,
                    task.error, task.dispatched_at, task.pr_opened_at,
                    task.completed_at, task.last_polled_at, task.id,
                ),
            )
            self._conn.commit()

    def add_event(self, task_id: int, kind: str, detail: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (task_id, ts, kind, detail) VALUES (?,?,?,?)",
                (task_id, now_iso(), kind, detail),
            )
            self._conn.commit()

    def list_events(self, task_id: int | None = None, limit: int = 200) -> list[dict]:
        if task_id is not None:
            cur = self._conn.execute(
                "SELECT * FROM events WHERE task_id=? ORDER BY ts DESC LIMIT ?",
                (task_id, limit),
            )
        else:
            cur = self._conn.execute(
                "SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,)
            )
        return [dict(r) for r in cur.fetchall()]

    def close(self) -> None:
        self._conn.close()
