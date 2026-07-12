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


def test_task_detail_workflow_tracks_checklist_evidence_and_updates(settings):
    store = PmtStore(settings.pmt_db_path)
    store.initialize()
    task = store.create_task(
        TaskInput(
            title="Employee access",
            acceptance_criteria=("Self Service reads own employee",),
            required_checks=("access-matrix", "prepush-quality"),
        )
    )

    criterion = task["acceptance_criteria"][0]
    assert criterion["done"] is False
    updated = store.update_task(
        task["task_key"],
        actor="web-admin",
        title="Employee access rights",
        description="Restrict employee visibility",
        project="HMX",
        module="core_hr",
        menu="Employee",
        assignee="Farhan",
        priority="high",
        target_branch="Human-Resources",
        source_branch="feat/employee-access",
        commit_ref="abc1234",
        mr_url="https://gitlab.example.test/hmx/-/merge_requests/1",
        pipeline_url="https://gitlab.example.test/hmx/-/pipelines/2",
    )
    checked = store.toggle_acceptance_criterion(task["task_key"], criterion["id"], "web-admin")
    with_second = store.add_acceptance_criterion(
        task["task_key"], "Supervisor reads subordinates", "web-admin"
    )
    evidence = store.add_evidence(
        task["task_key"],
        evidence_type="test",
        label="Access matrix passed",
        url="https://example.test/evidence/1",
        note="8 cases passed",
        actor="web-admin",
    )

    assert updated["title"] == "Employee access rights"
    assert updated["source_branch"] == "feat/employee-access"
    assert checked["acceptance_criteria"][0]["done"] is True
    assert len(with_second["acceptance_criteria"]) == 2
    assert evidence["evidence_type"] == "test"
    assert store.list_evidence(task["task_key"])[0]["label"] == "Access matrix passed"
    event_types = {event["event_type"] for event in store.task_events(task["task_key"])}
    assert {"task.updated", "criterion.toggled", "criterion.added", "evidence.added"} <= event_types


def test_task_detail_rejects_unsafe_evidence_url(settings):
    store = PmtStore(settings.pmt_db_path)
    store.initialize()
    task = store.create_task(TaskInput(title="Unsafe URL"))

    with pytest.raises(ValueError, match="http or https"):
        store.add_evidence(
            task["task_key"],
            evidence_type="note",
            label="bad",
            url="javascript:alert(1)",
            note="",
            actor="web-admin",
        )


def test_task_update_rejects_stale_version(settings):
    store = PmtStore(settings.pmt_db_path)
    store.initialize()
    task = store.create_task(TaskInput(title="Versioned task"))
    fields = {
        "actor": "web-admin",
        "description": "",
        "project": "HMX",
        "module": "core_hr",
        "menu": "",
        "assignee": "",
        "priority": "normal",
        "target_branch": "Human-Resources",
        "source_branch": "",
        "commit_ref": "",
        "mr_url": "",
        "pipeline_url": "",
    }
    store.update_task(task["task_key"], title="First update", expected_version=1, **fields)

    with pytest.raises(PermissionError, match="changed since"):
        store.update_task(task["task_key"], title="Stale update", expected_version=1, **fields)


def test_manual_status_transition_releases_agent_claim(settings):
    store = PmtStore(settings.pmt_db_path)
    store.initialize()
    task = store.create_task(TaskInput(title="Release me"))
    store.register_agent("agent-a", "server-a")
    store.claim_task(task["task_key"], "agent-a", "release-claim", 600)

    released = store.admin_transition_task(task["task_key"], "todo", "web-admin", "Return to queue")

    assert released["status"] == "todo"
    assert released["claimed_by"] is None
    assert released["lease_expires_at"] is None


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
