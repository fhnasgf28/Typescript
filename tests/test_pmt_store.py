from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

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
    claimed = store.claim_task(task["task_key"], "agent-a", "claim-flow", 600)
    run_id = claimed["current_run_id"]

    started = store.transition_task(
        task["task_key"], "agent-a", run_id, "in_progress", note="Inspecting source"
    )
    heartbeat = store.heartbeat(task["task_key"], "agent-a", run_id, 900)
    review = store.transition_task(
        task["task_key"], "agent-a", run_id, "ready_for_review", note="Checks passed"
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


def test_claim_requires_registered_active_agent(settings):
    store = PmtStore(settings.pmt_db_path)
    store.initialize()
    task = store.create_task(TaskInput(title="Guarded claim"))

    with pytest.raises(PermissionError, match="register"):
        store.claim_task(task["task_key"], "agent-a", "missing-agent", 600)

    store.register_agent("agent-a", "server-a")
    store.set_agent_mode("agent-a", "draining", "web-admin")
    with pytest.raises(PermissionError, match="draining"):
        store.claim_task(task["task_key"], "agent-a", "draining-agent", 600)


def test_expired_lease_is_fenced_and_reconciled(settings):
    store = PmtStore(settings.pmt_db_path)
    store.initialize()
    task = store.create_task(TaskInput(title="Expiring task"))
    store.register_agent("agent-a", "server-a")
    claimed = store.claim_task(task["task_key"], "agent-a", "expiring-claim", 600)
    run_id = claimed["current_run_id"]
    expired_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    with store._connect() as db:
        db.execute(
            "UPDATE tasks SET lease_expires_at=? WHERE id=?",
            (expired_at, task["id"]),
        )

    with pytest.raises(PermissionError, match="lease expired"):
        store.heartbeat(task["task_key"], "agent-a", run_id, 600)
    with pytest.raises(PermissionError, match="lease expired"):
        store.transition_task(task["task_key"], "agent-a", run_id, "in_progress")

    reconciled = store.reconcile_expired_leases()
    released = store.get_task(task["task_key"])
    assert reconciled["count"] == 1
    assert released["status"] == "todo"
    assert released["claimed_by"] is None
    assert released["current_run_id"] is None
    with store._connect() as db:
        run = db.execute(
            "SELECT status,finished_at FROM task_runs WHERE id=?", (run_id,)
        ).fetchone()
    assert run["status"] == "lease_expired"
    assert run["finished_at"] is not None


def test_stale_run_fencing_token_cannot_mutate_reclaimed_task(settings):
    store = PmtStore(settings.pmt_db_path)
    store.initialize()
    task = store.create_task(TaskInput(title="Fenced task"))
    store.register_agent("agent-a", "server-a")
    store.register_agent("agent-b", "server-b")
    first = store.claim_task(task["task_key"], "agent-a", "first-run", 600)
    with store._connect() as db:
        db.execute(
            "UPDATE tasks SET lease_expires_at=? WHERE id=?",
            ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), task["id"]),
        )
    second = store.claim_task(task["task_key"], "agent-b", "second-run", 600)

    assert second["current_run_id"] != first["current_run_id"]
    with pytest.raises(PermissionError):
        store.heartbeat(task["task_key"], "agent-a", first["current_run_id"], 600)
    with store._connect() as db:
        prior = db.execute(
            "SELECT status,finished_at FROM task_runs WHERE id=?", (first["current_run_id"],)
        ).fetchone()
    assert prior["status"] == "lease_expired"
    assert prior["finished_at"] is not None


def test_agent_control_center_derives_status_and_current_task(settings):
    store = PmtStore(settings.pmt_db_path)
    store.initialize()
    task = store.create_task(TaskInput(title="Agent task"))
    store.register_agent("agent-a", "server-a", ["hmx-code", "pipeline"])
    store.claim_task(task["task_key"], "agent-a", "agent-center", 600)

    agent = store.list_agents()[0]
    assert agent["effective_status"] == "busy"
    assert agent["current_task"]["task_key"] == task["task_key"]
    assert agent["lease_remaining_seconds"] > 0
    assert agent["capabilities"] == ["hmx-code", "pipeline"]


def test_schedule_finish_rejects_unknown_run(settings):
    store = PmtStore(settings.pmt_db_path)
    store.initialize()
    schedule = store.create_schedule("Sheet sync", "google_sheet_sync", 60, {}, "admin")
    with store._connect() as db:
        db.execute(
            "UPDATE schedules SET next_run_at='2000-01-01T00:00:00+00:00' WHERE id=?",
            (schedule["id"],),
        )
    store.claim_due_schedule("worker-a")

    with pytest.raises(PermissionError, match="fencing token is stale"):
        store.finish_schedule_run(schedule["id"], "unknown-run", "worker-a", "succeeded", {})


def test_expired_schedule_run_is_timed_out_before_reclaim(settings):
    store = PmtStore(settings.pmt_db_path)
    store.initialize()
    schedule = store.create_schedule("Lease recovery", "lease_recovery", 60, {}, "admin")
    with store._connect() as db:
        db.execute(
            "UPDATE schedules SET next_run_at='2000-01-01T00:00:00+00:00' WHERE id=?",
            (schedule["id"],),
        )
    first = store.claim_due_schedule("worker-a", 60)
    with store._connect() as db:
        db.execute(
            "UPDATE schedules SET lock_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",
            (schedule["id"],),
        )

    second = store.claim_due_schedule("worker-b", 60)
    runs = store.list_schedule_runs(schedule["id"])

    assert first["run_id"] != second["run_id"]
    assert {run["status"] for run in runs} == {"running", "timed_out"}


def test_stale_schedule_run_cannot_finish_after_same_worker_reclaims(settings):
    store = PmtStore(settings.pmt_db_path)
    store.initialize()
    schedule = store.create_schedule("Lease recovery", "lease_recovery", 60, {}, "admin")
    with store._connect() as db:
        db.execute(
            "UPDATE schedules SET next_run_at='2000-01-01T00:00:00+00:00' WHERE id=?",
            (schedule["id"],),
        )
    first = store.claim_due_schedule("worker-a", 60)
    with store._connect() as db:
        db.execute(
            "UPDATE schedules SET lock_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",
            (schedule["id"],),
        )
    second = store.claim_due_schedule("worker-a", 60)

    with pytest.raises(PermissionError, match="fencing token is stale"):
        store.finish_schedule_run(schedule["id"], first["run_id"], "worker-a", "succeeded", {})

    current = next(item for item in store.list_schedules() if item["id"] == schedule["id"])
    assert current["current_run_id"] == second["run_id"]
    assert current["locked_by"] == "worker-a"


def test_initialize_backfills_unambiguous_legacy_active_run(settings):
    store = PmtStore(settings.pmt_db_path)
    store.initialize()
    store.register_agent("agent-a", "server-a", ["code"])
    task = store.create_task(TaskInput(title="Legacy active task"), actor="admin")
    claimed = store.claim_task(task["task_key"], "agent-a", "legacy-claim", 600)
    with store._connect() as db:
        db.execute("UPDATE tasks SET current_run_id=NULL WHERE id=?", (task["id"],))

    store.initialize()

    migrated = store.get_task(task["task_key"])
    assert migrated["current_run_id"] == claimed["current_run_id"]
    assert migrated["claimed_by"] == "agent-a"


def test_concurrent_store_initialization_is_safe(settings):
    stores = [PmtStore(settings.pmt_db_path) for _ in range(6)]

    with ThreadPoolExecutor(max_workers=6) as executor:
        list(executor.map(lambda store: store.initialize(), stores))

    with stores[0]._connect() as db:
        task_columns = {row["name"] for row in db.execute("PRAGMA table_info(tasks)")}
        agent_columns = {row["name"] for row in db.execute("PRAGMA table_info(agents)")}
        schedule_columns = {row["name"] for row in db.execute("PRAGMA table_info(schedules)")}
    assert {"current_run_id", "source_branch", "pipeline_url"} <= task_columns
    assert "mode" in agent_columns
    assert "current_run_id" in schedule_columns
