from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from mcp_transfer_node.config import load_settings
from mcp_transfer_node.pmt_sheet import sync_google_sheet
from mcp_transfer_node.pmt_store import PmtStore


async def run_once(worker_id: str) -> dict[str, object]:
    settings = load_settings()
    store = PmtStore(settings.pmt_db_path)
    store.initialize()
    maintenance = {
        "tasks": store.reconcile_expired_leases(actor=worker_id),
        "approvals": store.reconcile_expired_approval_leases(actor=worker_id),
    }
    schedule = store.claim_due_schedule(worker_id)
    if schedule is None:
        return {"status": "idle", "message": "no due schedule", "maintenance": maintenance}

    run_id = str(schedule["run_id"])
    try:
        if schedule["job_type"] == "google_sheet_sync":
            result = await sync_google_sheet(store, schedule["payload"], actor=worker_id)
        elif schedule["job_type"] == "lease_recovery":
            result = maintenance
        else:
            result = {"reason": f"unsupported job type: {schedule['job_type']}"}
            store.finish_schedule_run(schedule["id"], run_id, worker_id, "skipped", result)
            return {"status": "skipped", "schedule": schedule["id"], "result": result}
    except Exception as exc:
        safe_result = {"error_type": type(exc).__name__, "message": str(exc)[:500]}
        store.finish_schedule_run(schedule["id"], run_id, worker_id, "failed", safe_result)
        return {"status": "failed", "schedule": schedule["id"], "result": safe_result}

    store.finish_schedule_run(schedule["id"], run_id, worker_id, "succeeded", result)
    return {"status": "succeeded", "schedule": schedule["id"], "result": result}


def run() -> None:
    parser = argparse.ArgumentParser(description="Run one due standalone PMT schedule")
    parser.add_argument("--worker-id", default=os.environ.get("MCP_PMT_AGENT_ID", ""))
    args = parser.parse_args()
    if not args.worker_id:
        parser.error("--worker-id or MCP_PMT_AGENT_ID is required")
    result = asyncio.run(run_once(args.worker_id))
    print(json.dumps(result, ensure_ascii=False))
    if result["status"] == "failed":
        sys.exit(1)


if __name__ == "__main__":
    run()
