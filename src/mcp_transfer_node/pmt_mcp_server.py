from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("standalone-pmt")


def _agent_id() -> str:
    value = os.environ.get("MCP_PMT_AGENT_ID", "").strip()
    if not value:
        raise ValueError("MCP_PMT_AGENT_ID is required")
    return value


def _api_config() -> tuple[str, str]:
    url = os.environ.get("MCP_PMT_API_URL", "").strip().rstrip("/")
    token = os.environ.get("MCP_PMT_API_TOKEN", "").strip()
    if not url or not token:
        raise ValueError("MCP_PMT_API_URL and MCP_PMT_API_TOKEN are required")
    parsed = urlparse(url)
    local_hosts = {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in local_hosts
    ):
        raise ValueError("MCP_PMT_API_URL must use HTTPS except for localhost")
    return url, token


def _request(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url, token = _api_config()
    headers = {
        "Authorization": f"Bearer {token}",
        "X-PMT-Agent": _agent_id(),
    }
    try:
        response = httpx.request(
            method,
            f"{url}/api/v1/pmt{path}",
            headers=headers,
            json=json_body,
            params=params,
            timeout=30,
        )
        payload = response.json()
    except httpx.TimeoutException as exc:
        raise RuntimeError("PMT API request timed out") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError("PMT API returned an invalid response") from exc
    if response.status_code >= 400 or payload.get("success") is not True:
        error = payload.get("error") if isinstance(payload, dict) else None
        code = error.get("code", "PMT_API_ERROR") if isinstance(error, dict) else "PMT_API_ERROR"
        message = (
            error.get("message", "request failed") if isinstance(error, dict) else "request failed"
        )
        raise RuntimeError(f"{code}: {message}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("PMT API returned an invalid data envelope")
    return data


@mcp.tool()
def pmt_get_available_tasks(limit: int = 20) -> dict[str, object]:
    """List unclaimed To-Do tasks ordered by priority."""
    return _request("GET", "/tasks", params={"status": "todo", "limit": limit})


@mcp.tool()
def pmt_get_my_tasks(limit: int = 20) -> dict[str, object]:
    """List tasks currently claimed by this MCP agent identity."""
    return _request("GET", "/tasks", params={"claimed_by": _agent_id(), "limit": limit})


@mcp.tool()
def pmt_get_task(task_ref: str) -> dict[str, object]:
    """Read a task by PMT key or internal ID."""
    return _request("GET", f"/tasks/{task_ref}")


@mcp.tool()
def pmt_get_task_context(task_ref: str) -> dict[str, object]:
    """Return task requirements, HMX context, checks, claim, and recent audit events."""
    task = _request("GET", f"/tasks/{task_ref}")["task"]
    events = _request("GET", f"/tasks/{task_ref}/events")["events"]
    return {
        "task": task,
        "hmx": {
            "project": task["project"],
            "module": task["module"],
            "menu": task["menu"],
            "targetBranch": task["target_branch"],
        },
        "approvalGates": {
            "push": True,
            "createMergeRequest": True,
            "externalStatusWrite": True,
            "sendMessage": True,
            "deployment": True,
        },
        "events": events,
    }


@mcp.tool()
def pmt_create_task(
    title: str,
    description: str = "",
    project: str = "HMX",
    module: str = "",
    menu: str = "",
    assignee: str = "Farhan",
    priority: str = "normal",
    target_branch: str = "Human-Resources",
    acceptance_criteria: list[str] | None = None,
    required_checks: list[str] | None = None,
) -> dict[str, object]:
    """Create a PMT task. Treat as an administrative write operation."""
    return _request(
        "POST",
        "/tasks",
        json_body={
            "title": title,
            "description": description,
            "project": project,
            "module": module,
            "menu": menu,
            "assignee": assignee,
            "priority": priority,
            "target_branch": target_branch,
            "acceptance_criteria": acceptance_criteria or [],
            "required_checks": required_checks or [],
        },
    )


@mcp.tool()
def pmt_register_agent(
    server_name: str, capabilities: list[str] | None = None
) -> dict[str, object]:
    """Register or heartbeat this MCP process as an available PMT agent."""
    return _request(
        "POST",
        "/agents/register",
        json_body={
            "agent_id": _agent_id(),
            "server_name": server_name,
            "capabilities": capabilities or [],
        },
    )


@mcp.tool()
def pmt_claim_task(
    task_ref: str, idempotency_key: str, lease_seconds: int = 1800
) -> dict[str, object]:
    """Atomically claim a task; safe to retry with the same idempotency key."""
    return _request(
        "POST",
        f"/tasks/{task_ref}/claim",
        json_body={
            "agent_id": _agent_id(),
            "idempotency_key": idempotency_key,
            "lease_seconds": lease_seconds,
        },
    )


def _transition(task_ref: str, target_status: str, note: str = "", blocker: str = ""):
    return _request(
        "POST",
        f"/tasks/{task_ref}/transition",
        json_body={
            "agent_id": _agent_id(),
            "status": target_status,
            "note": note,
            "blocker": blocker,
        },
    )


@mcp.tool()
def pmt_task_heartbeat(task_ref: str, lease_seconds: int = 1800) -> dict[str, object]:
    """Extend the current agent's task lease."""
    return _request(
        "POST",
        f"/tasks/{task_ref}/heartbeat",
        json_body={"agent_id": _agent_id(), "lease_seconds": lease_seconds},
    )


@mcp.tool()
def pmt_start_task(task_ref: str, note: str = "") -> dict[str, object]:
    """Move a claimed task to In Progress."""
    return _transition(task_ref, "in_progress", note)


@mcp.tool()
def pmt_update_progress(task_ref: str, note: str) -> dict[str, object]:
    """Record current work progress while preserving In Progress status."""
    return _transition(task_ref, "in_progress", note)


@mcp.tool()
def pmt_report_blocker(task_ref: str, blocker: str, note: str = "") -> dict[str, object]:
    """Mark an owned task Blocked and preserve its agent lease."""
    return _transition(task_ref, "blocked", note, blocker)


@mcp.tool()
def pmt_submit_for_review(task_ref: str, summary: str) -> dict[str, object]:
    """Release an owned task into Ready for Review."""
    return _transition(task_ref, "ready_for_review", summary)


@mcp.tool()
def pmt_release_task(task_ref: str, note: str = "") -> dict[str, object]:
    """Release a claimed task back to To-Do."""
    return _transition(task_ref, "todo", note)


@mcp.tool()
def pmt_get_schedules() -> dict[str, object]:
    """List durable PMT schedules and their latest state."""
    return _request("GET", "/schedules")


@mcp.tool()
def pmt_create_schedule(
    name: str, job_type: str, interval_seconds: int, payload: dict[str, Any] | None = None
) -> dict[str, object]:
    """Create a durable interval schedule for a PMT worker."""
    return _request(
        "POST",
        "/schedules",
        json_body={
            "name": name,
            "job_type": job_type,
            "interval_seconds": interval_seconds,
            "payload": payload or {},
        },
    )


@mcp.tool()
def pmt_claim_due_schedule(lease_seconds: int = 300) -> dict[str, object]:
    """Atomically claim one due schedule for execution by this agent."""
    return _request(
        "POST",
        "/schedules/claim-due",
        json_body={"worker_id": _agent_id(), "lease_seconds": lease_seconds},
    )


@mcp.tool()
def pmt_finish_schedule(
    schedule_id: str,
    run_id: str,
    status: str,
    result: dict[str, Any] | None = None,
) -> dict[str, object]:
    """Finish a claimed schedule run and compute its next run time."""
    return _request(
        "POST",
        f"/schedules/{schedule_id}/finish",
        json_body={
            "worker_id": _agent_id(),
            "run_id": run_id,
            "status": status,
            "result": result or {},
        },
    )


def run() -> None:
    mcp.run()
