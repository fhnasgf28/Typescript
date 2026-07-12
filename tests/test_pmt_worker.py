from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from mcp_transfer_node import pmt_worker
from mcp_transfer_node.pmt_sheet import SheetSyncBusy
from mcp_transfer_node.pmt_store import PmtStore


def _git_push_payload() -> dict[str, str]:
    return {
        "repository": "hmx-002",
        "remote": "origin",
        "source_branch": "feat/approval-center",
        "target_branch": "Human-Resources",
        "commit_sha": "abc1234",
    }


def test_worker_reconciles_approval_lease_even_without_due_schedule(settings, monkeypatch):
    store = PmtStore(settings.pmt_db_path)
    store.initialize()
    approval = store.create_approval_request(
        action_type="git_push",
        title="Recover abandoned execution",
        reason="Executor crashed",
        payload=_git_push_payload(),
        requested_by="agent-a",
        idempotency_key="worker-recovery-request",
        admin_request=True,
    )
    store.decide_approval(approval["approval_key"], "approve", "Farhan", "", 1, 3600)
    store.register_agent("executor-a", "server-a", ["approval.execute"])
    store.claim_approval(approval["approval_key"], "executor-a", "worker-recovery-run", 600)
    with store._connect() as db:
        db.execute(
            "UPDATE approval_requests SET lease_expires_at=? WHERE id=?",
            ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), approval["id"]),
        )

    monkeypatch.setattr(pmt_worker, "load_settings", lambda: settings)
    result = asyncio.run(pmt_worker.run_once("maintenance-worker"))

    assert result["status"] == "idle"
    assert result["maintenance"]["approvals"]["count"] == 1
    assert store.get_approval(approval["approval_key"])["status"] == "approved"
    assert store.list_approval_runs(approval["approval_key"])[0]["status"] == "timed_out"


def test_worker_marks_busy_sheet_schedule_skipped_without_failure_details(settings, monkeypatch):
    store = PmtStore(settings.pmt_db_path)
    store.initialize()
    schedule = store.create_schedule(
        "busy sync",
        "google_sheet_sync",
        300,
        {"csv_url": "https://docs.google.com/spreadsheets/d/example/export?format=csv"},
        "admin",
    )

    with store._transaction() as db:
        db.execute(
            "UPDATE schedules SET next_run_at=? WHERE id=?",
            ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), schedule["id"]),
        )

    async def busy(*_args, **_kwargs):
        raise SheetSyncBusy("PRIVATE_LEASE_OWNER")

    monkeypatch.setattr(pmt_worker, "load_settings", lambda: settings)
    monkeypatch.setattr(pmt_worker, "sync_google_sheet", busy)
    result = asyncio.run(pmt_worker.run_once("schedule-worker"))

    assert result["status"] == "skipped"
    assert "PRIVATE_LEASE_OWNER" not in str(result)
    runs = store.list_schedule_runs(schedule_id=schedule["id"])
    assert runs[0]["status"] == "skipped"
