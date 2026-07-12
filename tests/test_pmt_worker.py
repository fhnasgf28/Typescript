from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from mcp_transfer_node import pmt_worker
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
