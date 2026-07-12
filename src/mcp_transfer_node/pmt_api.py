from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from mcp_transfer_node.auth import authenticate_peer
from mcp_transfer_node.config import AllowedPeer, TransferSettings, load_allowed_peers
from mcp_transfer_node.pmt_store import PmtStore, TaskInput
from mcp_transfer_node.responses import error_response, success_response

logger = logging.getLogger(__name__)


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=20_000)
    project: str = Field(default="HMX", max_length=120)
    module: str = Field(default="", max_length=120)
    menu: str = Field(default="", max_length=240)
    source: str = Field(default="api", max_length=80)
    external_id: str = Field(default="", max_length=240)
    assignee: str = Field(default="", max_length=120)
    priority: str = "normal"
    target_branch: str = Field(default="Human-Resources", max_length=240)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=100)
    required_checks: list[str] = Field(default_factory=list, max_length=100)


class AgentRegistration(BaseModel):
    agent_id: str = Field(min_length=1, max_length=120)
    server_name: str = Field(min_length=1, max_length=120)
    capabilities: list[str] = Field(default_factory=list, max_length=100)


class TaskClaim(BaseModel):
    agent_id: str = Field(min_length=1, max_length=120)
    idempotency_key: str = Field(min_length=1, max_length=240)
    lease_seconds: int = Field(default=1800, ge=60, le=7200)


class TaskHeartbeat(BaseModel):
    agent_id: str = Field(min_length=1, max_length=120)
    lease_seconds: int = Field(default=1800, ge=60, le=7200)


class TaskTransition(BaseModel):
    agent_id: str = Field(min_length=1, max_length=120)
    status: str
    note: str = Field(default="", max_length=20_000)
    blocker: str = Field(default="", max_length=20_000)


class ScheduleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    job_type: str = Field(min_length=1, max_length=120)
    interval_seconds: int = Field(ge=60, le=2_678_400)
    payload: dict[str, Any] = Field(default_factory=dict)


class ScheduleClaim(BaseModel):
    worker_id: str = Field(min_length=1, max_length=120)
    lease_seconds: int = Field(default=300, ge=60, le=7200)


class ScheduleFinish(BaseModel):
    worker_id: str = Field(min_length=1, max_length=120)
    run_id: str = Field(min_length=1, max_length=120)
    status: str
    result: dict[str, Any] = Field(default_factory=dict)


def _peers(settings: TransferSettings) -> list[AllowedPeer]:
    return load_allowed_peers(settings.config_dir / "peers.json")


def create_pmt_api_router(settings: TransferSettings) -> APIRouter:
    store = PmtStore(settings.pmt_db_path)
    store.initialize()
    router = APIRouter(prefix="/api/v1/pmt", tags=["PMT"])

    def require_agent(
        authorization: str | None = Header(default=None),
        x_pmt_agent: str | None = Header(default=None),
        x_transfer_source: str | None = Header(default=None),
    ) -> AllowedPeer:
        source = x_pmt_agent or x_transfer_source
        if not authorization or not authorization.startswith("Bearer ") or not source:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=error_response("UNAUTHORIZED", "Invalid or missing agent credentials"),
            )
        peer = authenticate_peer(
            authorization.removeprefix("Bearer ").strip(), source, _peers(settings)
        )
        if peer is None:
            logger.warning("PMT request rejected agent=%s reason=invalid_credentials", source)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=error_response("UNAUTHORIZED", "Invalid or missing agent credentials"),
            )
        return peer

    def actor_matches(peer: AllowedPeer, agent_id: str) -> None:
        if peer.name != agent_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=error_response(
                    "FORBIDDEN", "Agent may only act as its authenticated identity"
                ),
            )

    def translate_error(exc: Exception) -> None:
        if isinstance(exc, KeyError):
            code = status.HTTP_404_NOT_FOUND
            label = "NOT_FOUND"
        elif isinstance(exc, PermissionError):
            code = status.HTTP_409_CONFLICT
            label = "CLAIM_CONFLICT"
        else:
            code = status.HTTP_400_BAD_REQUEST
            label = "INVALID_REQUEST"
        raise HTTPException(code, detail=error_response(label, str(exc))) from exc

    @router.get("/tasks")
    def list_tasks(
        task_status: str | None = Query(default=None, alias="status"),
        assignee: str | None = None,
        claimed_by: str | None = None,
        limit: int = Query(default=50, ge=1, le=200),
        _: AllowedPeer = Depends(require_agent),
    ) -> dict[str, object]:
        try:
            tasks = store.list_tasks(
                status=task_status, assignee=assignee, claimed_by=claimed_by, limit=limit
            )
        except ValueError as exc:
            translate_error(exc)
        return success_response({"tasks": tasks})

    @router.post("/tasks", status_code=status.HTTP_201_CREATED)
    def create_task(payload: TaskCreate, peer: AllowedPeer = Depends(require_agent)):
        try:
            task = store.create_task(
                TaskInput(
                    title=payload.title,
                    description=payload.description,
                    project=payload.project,
                    module=payload.module,
                    menu=payload.menu,
                    source=payload.source,
                    external_id=payload.external_id,
                    assignee=payload.assignee,
                    priority=payload.priority,
                    target_branch=payload.target_branch,
                    acceptance_criteria=tuple(payload.acceptance_criteria),
                    required_checks=tuple(payload.required_checks),
                ),
                actor=peer.name,
            )
        except ValueError as exc:
            translate_error(exc)
        return success_response({"task": task})

    @router.get("/tasks/{task_ref}")
    def get_task(task_ref: str, _: AllowedPeer = Depends(require_agent)):
        task = store.get_task(task_ref)
        if task is None:
            translate_error(KeyError(task_ref))
        return success_response({"task": task})

    @router.get("/tasks/{task_ref}/events")
    def get_task_events(task_ref: str, _: AllowedPeer = Depends(require_agent)):
        try:
            events = store.task_events(task_ref)
        except KeyError as exc:
            translate_error(exc)
        return success_response({"events": events})

    @router.post("/agents/register")
    def register_agent(payload: AgentRegistration, peer: AllowedPeer = Depends(require_agent)):
        actor_matches(peer, payload.agent_id)
        try:
            agent = store.register_agent(
                payload.agent_id, payload.server_name, payload.capabilities
            )
        except ValueError as exc:
            translate_error(exc)
        return success_response({"agent": agent})

    @router.post("/tasks/{task_ref}/claim")
    def claim_task(task_ref: str, payload: TaskClaim, peer: AllowedPeer = Depends(require_agent)):
        actor_matches(peer, payload.agent_id)
        try:
            task = store.claim_task(
                task_ref,
                payload.agent_id,
                payload.idempotency_key,
                payload.lease_seconds,
            )
        except (KeyError, PermissionError, ValueError) as exc:
            translate_error(exc)
        return success_response({"task": task})

    @router.post("/tasks/{task_ref}/heartbeat")
    def heartbeat(
        task_ref: str, payload: TaskHeartbeat, peer: AllowedPeer = Depends(require_agent)
    ):
        actor_matches(peer, payload.agent_id)
        try:
            task = store.heartbeat(task_ref, payload.agent_id, payload.lease_seconds)
        except (KeyError, PermissionError, ValueError) as exc:
            translate_error(exc)
        return success_response({"task": task})

    @router.post("/tasks/{task_ref}/transition")
    def transition(
        task_ref: str, payload: TaskTransition, peer: AllowedPeer = Depends(require_agent)
    ):
        actor_matches(peer, payload.agent_id)
        try:
            task = store.transition_task(
                task_ref,
                payload.agent_id,
                payload.status,
                note=payload.note,
                blocker=payload.blocker,
            )
        except (KeyError, PermissionError, ValueError) as exc:
            translate_error(exc)
        return success_response({"task": task})

    @router.get("/schedules")
    def list_schedules(_: AllowedPeer = Depends(require_agent)):
        return success_response({"schedules": store.list_schedules()})

    @router.post("/schedules", status_code=status.HTTP_201_CREATED)
    def create_schedule(payload: ScheduleCreate, peer: AllowedPeer = Depends(require_agent)):
        try:
            schedule = store.create_schedule(
                payload.name,
                payload.job_type,
                payload.interval_seconds,
                payload.payload,
                peer.name,
            )
        except ValueError as exc:
            translate_error(exc)
        return success_response({"schedule": schedule})

    @router.post("/schedules/claim-due")
    def claim_due_schedule(payload: ScheduleClaim, peer: AllowedPeer = Depends(require_agent)):
        actor_matches(peer, payload.worker_id)
        schedule = store.claim_due_schedule(payload.worker_id, payload.lease_seconds)
        return success_response({"schedule": schedule})

    @router.post("/schedules/{schedule_id}/finish")
    def finish_schedule(
        schedule_id: str,
        payload: ScheduleFinish,
        peer: AllowedPeer = Depends(require_agent),
    ):
        actor_matches(peer, payload.worker_id)
        try:
            schedule = store.finish_schedule_run(
                schedule_id,
                payload.run_id,
                payload.worker_id,
                payload.status,
                payload.result,
            )
        except (KeyError, PermissionError, ValueError) as exc:
            translate_error(exc)
        return success_response({"schedule": schedule})

    return router
