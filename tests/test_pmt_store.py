from __future__ import annotations

from datetime import datetime, timezone

import pytest

from mcp_transfer_node.pmt_store import PmtStore, TaskInput


def test_create_multiple_manual_tasks_and_list_by_priority(settings):
    store = PmtStore(settings.pmt_db_path)
    store.initialize()
    first = store.create_task(TaskInput(title="Normal task"))
    urgent = store.create_task(TaskInput(title="Urgent task", priority="urgent"))

    tasks = store.list_tasks(status="todo")

    assert first["task_key"] == "PMT-0001"
    assert urgent["task_key"] == "PMT-0002"
    assert [task["title"] for task in tasks] == ["Urgent task", "Normal task"]


def test_external_task_creation_is_idempotent(settings):
    store = PmtStore(settings.pmt_db_path)
    store.initialize()
    payload = TaskInput(title="Sheet task", source="google_sheet", external_id="sheet:88")

    first = store.create_task(payload)
    second = store.create_task(payload)

    assert first["id"] == second["id"]
    assert len(store.list_tasks()) == 1


def test_atomic_claim_is_idempotent_and_rejects_other_agent(settings):
    store = PmtStore(settings.pmt_db_path)
    store.initialize()
    task = store.create_task(TaskInput(title="Access task"))
    store.register_agent("agent-a", "server-a")
    store.register_agent("agent-b", "server-b")

    claimed = store.claim_task(task["task_key"], "agent-a", "claim-1", 600)
    repeated = store.claim_task(task["task_key"], "agent-a", "claim-1", 600)

    assert claimed["claimed_by"] == "agent-a"
    assert repeated["id"] == claimed["id"]
    with pytest.raises(PermissionError, match="already claimed"):
        store.claim_task(task["task_key"], "agent-b", "claim-2", 600)


def test_owned_task_flow_and_audit_events(settings):
    store = PmtStore(settings.pmt_db_path)
    store.initialize()
    task = store.create_task(TaskInput(title="Employee form"))
    store.register_agent("agent-a", "server-a")
    store.claim_task(task["task_key"], "agent-a", "claim-flow", 600)

    started = store.transition_task(
        task["task_key"], "agent-a", "in_progress", note="Inspecting source"
    )
    heartbeat = store.heartbeat(task["task_key"], "agent-a", 900)
    review = store.transition_task(
        task["task_key"], "agent-a", "ready_for_review", note="Checks passed"
    )

    assert started["status"] == "in_progress"
    assert datetime.fromisoformat(heartbeat["lease_expires_at"]) > datetime.now(timezone.utc)
    assert review["status"] == "ready_for_review"
    assert review["claimed_by"] is None
    assert {event["event_type"] for event in store.task_events(task["task_key"])} >= {
        "task.created",
        "task.claimed",
        "task.in_progress",
        "task.ready_for_review",
    }


def test_schedule_claim_and_finish(settings):
    store = PmtStore(settings.pmt_db_path)
    store.initialize()
    schedule = store.create_schedule("Sheet sync", "google_sheet_sync", 60, {}, "admin")

    with store._connect() as db:  # force due time without sleeping
        db.execute(
            "UPDATE schedules SET next_run_at='2000-01-01T00:00:00+00:00' WHERE id=?",
            (schedule["id"],),
        )
    claimed = store.claim_due_schedule("worker-a")
    assert claimed is not None
    assert store.claim_due_schedule("worker-b") is None

    finished = store.finish_schedule_run(
        schedule["id"], claimed["run_id"], "worker-a", "succeeded", {"matched": 1}
    )
    assert finished["last_status"] == "succeeded"
    assert finished["locked_by"] is None
