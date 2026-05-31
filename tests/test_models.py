from app.models import TaskState, derive_state


def test_dispatched_when_new():
    assert derive_state(status="new", status_detail=None, pull_requests=[], structured_output=None) == TaskState.DISPATCHED


def test_running_without_pr():
    assert derive_state(status="running", status_detail="working", pull_requests=[], structured_output=None) == TaskState.RUNNING


def test_blocked_on_waiting_for_user():
    assert derive_state(status="running", status_detail="waiting_for_user", pull_requests=[], structured_output=None) == TaskState.BLOCKED


def test_pr_open_while_running():
    state = derive_state(
        status="running", status_detail="working",
        pull_requests=[{"url": "https://github.com/x/y/pull/1"}], structured_output=None,
    )
    assert state == TaskState.PR_OPEN


def test_dispatched_when_claimed():
    assert derive_state(status="claimed", status_detail=None, pull_requests=[], structured_output=None) == TaskState.DISPATCHED


def test_blocked_when_suspended():
    assert derive_state(status="suspended", status_detail="inactivity", pull_requests=[], structured_output=None) == TaskState.BLOCKED


def test_completed_when_finished_detail_with_pr():
    state = derive_state(
        status="running", status_detail="finished",
        pull_requests=[{"url": "https://github.com/x/y/pull/1"}], structured_output=None,
    )
    assert state == TaskState.COMPLETED
    assert state.is_terminal


def test_completed_when_exit_with_pr():
    state = derive_state(
        status="exit", status_detail=None,
        pull_requests=[{"url": "https://github.com/x/y/pull/1"}], structured_output=None,
    )
    assert state == TaskState.COMPLETED
    assert state.is_terminal


def test_completed_via_structured_output_even_without_pr_list():
    state = derive_state(
        status="running", status_detail="working", pull_requests=[],
        structured_output={"status": "completed", "pr_url": "https://github.com/x/y/pull/2"},
    )
    assert state == TaskState.COMPLETED


def test_failed_when_terminal_without_pr():
    state = derive_state(status="exit", status_detail=None, pull_requests=[], structured_output=None)
    assert state == TaskState.FAILED
    assert state.is_terminal


def test_state_helpers():
    assert TaskState.RUNNING.is_active
    assert not TaskState.COMPLETED.is_active
    assert TaskState.FAILED.is_terminal
