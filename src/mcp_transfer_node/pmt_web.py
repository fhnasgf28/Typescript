from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from mcp_transfer_node.config import TransferSettings
from mcp_transfer_node.pmt_sheet import validate_sheet_url
from mcp_transfer_node.pmt_store import (
    ADMIN_STATUS_TRANSITIONS,
    EVIDENCE_TYPES,
    PmtStore,
    TaskInput,
)

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _require_login(request: Request) -> None:
    if request.session.get("authenticated") is not True:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )


def create_pmt_web_router(settings: TransferSettings) -> APIRouter:
    store = PmtStore(settings.pmt_db_path)
    store.initialize()
    router = APIRouter(prefix="/pmt", tags=["PMT Web"])

    @router.get("", response_class=HTMLResponse)
    @router.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, task_status: str | None = None):
        _require_login(request)
        try:
            tasks = store.list_tasks(status=task_status, limit=200)
        except ValueError:
            tasks = store.list_tasks(limit=200)
        grouped = {
            name: [task for task in tasks if task["status"] == name]
            for name in (
                "inbox",
                "todo",
                "claimed",
                "in_progress",
                "ready_for_review",
                "blocked",
                "done",
            )
        }
        agents = store.list_agents()
        return TEMPLATES.TemplateResponse(
            request,
            "pmt_dashboard.html",
            {
                "settings": settings,
                "tasks": tasks,
                "grouped": grouped,
                "agents": agents,
                "online_agents": sum(
                    agent["effective_status"] in {"online", "busy", "draining"} for agent in agents
                ),
            },
        )

    @router.get("/agents", response_class=HTMLResponse)
    def agent_control_center(request: Request):
        _require_login(request)
        agents = store.list_agents()
        return TEMPLATES.TemplateResponse(
            request,
            "pmt_agents.html",
            {
                "settings": settings,
                "agents": agents,
                "counts": {
                    status_name: sum(agent["effective_status"] == status_name for agent in agents)
                    for status_name in (
                        "online",
                        "busy",
                        "draining",
                        "offline",
                        "disabled",
                    )
                },
            },
        )

    @router.post("/agents/{agent_id}/mode")
    def update_agent_mode(
        request: Request,
        agent_id: str,
        mode: str = Form(...),
    ) -> RedirectResponse:
        _require_login(request)
        try:
            store.set_agent_mode(agent_id, mode, "web-admin")
        except KeyError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Agent tidak ditemukan") from exc
        except ValueError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return RedirectResponse("/pmt/agents", status_code=status.HTTP_303_SEE_OTHER)

    @router.post("/agents/reconcile-leases")
    def reconcile_agent_leases(request: Request) -> RedirectResponse:
        _require_login(request)
        store.reconcile_expired_leases("web-admin")
        return RedirectResponse("/pmt/agents", status_code=status.HTTP_303_SEE_OTHER)

    @router.get("/sync", response_class=HTMLResponse)
    def sync_center(request: Request):
        _require_login(request)
        schedules = [
            schedule
            for schedule in store.list_schedules()
            if schedule["job_type"] == "google_sheet_sync"
        ]
        return TEMPLATES.TemplateResponse(
            request,
            "pmt_sync.html",
            {
                "settings": settings,
                "schedules": schedules,
                "runs": store.list_schedule_runs(limit=50),
            },
        )

    @router.post("/sync/schedules")
    def create_sync_schedule(
        request: Request,
        name: str = Form(...),
        csv_url: str = Form(...),
        interval_minutes: int = Form(default=15),
        assignee: str = Form(default="Farhan"),
        dev_status: str = Form(default="To-Do"),
        project: str = Form(default="HMX"),
        target_branch: str = Form(default="Human-Resources"),
    ) -> RedirectResponse:
        _require_login(request)
        try:
            csv_url = validate_sheet_url(csv_url)
            store.create_schedule(
                name,
                "google_sheet_sync",
                max(1, interval_minutes) * 60,
                {
                    "csv_url": csv_url,
                    "assignee": assignee,
                    "dev_status": dev_status,
                    "project": project,
                    "target_branch": target_branch,
                },
                "web-admin",
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return RedirectResponse("/pmt/sync", status_code=status.HTTP_303_SEE_OTHER)

    @router.post("/sync/schedules/{schedule_id}/toggle")
    def toggle_sync_schedule(
        request: Request,
        schedule_id: str,
        enabled: bool = Form(...),
    ) -> RedirectResponse:
        _require_login(request)
        try:
            store.set_schedule_enabled(schedule_id, enabled)
        except KeyError as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, detail="Schedule tidak ditemukan"
            ) from exc
        return RedirectResponse("/pmt/sync", status_code=status.HTTP_303_SEE_OTHER)

    @router.post("/tasks")
    def create_task(
        request: Request,
        title: str = Form(...),
        description: str = Form(default=""),
        project: str = Form(default="HMX"),
        module: str = Form(default=""),
        menu: str = Form(default=""),
        priority: str = Form(default="normal"),
        assignee: str = Form(default="Farhan"),
        target_branch: str = Form(default="Human-Resources"),
    ) -> RedirectResponse:
        _require_login(request)
        try:
            store.create_task(
                TaskInput(
                    title=title,
                    description=description,
                    project=project,
                    module=module,
                    menu=menu,
                    priority=priority,
                    assignee=assignee,
                    target_branch=target_branch,
                ),
                actor="web-admin",
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return RedirectResponse("/pmt", status_code=status.HTTP_303_SEE_OTHER)

    @router.get("/tasks/{task_ref}", response_class=HTMLResponse)
    def task_detail(request: Request, task_ref: str):
        _require_login(request)
        task = store.get_task(task_ref)
        if task is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Task tidak ditemukan")
        allowed_statuses = set(ADMIN_STATUS_TRANSITIONS.get(task["status"], set()))
        if not task["claimed_by"]:
            allowed_statuses -= {"claimed", "in_progress"}
        return TEMPLATES.TemplateResponse(
            request,
            "pmt_task_detail.html",
            {
                "settings": settings,
                "task": task,
                "events": store.task_events(task_ref),
                "evidence": store.list_evidence(task_ref),
                "statuses": [task["status"]]
                + [
                    status_name
                    for status_name in (
                        "inbox",
                        "todo",
                        "claimed",
                        "in_progress",
                        "ready_for_review",
                        "blocked",
                        "done",
                        "cancelled",
                    )
                    if status_name in allowed_statuses
                ],
                "evidence_types": sorted(EVIDENCE_TYPES),
            },
        )

    @router.post("/tasks/{task_ref}/edit")
    def edit_task(
        request: Request,
        task_ref: str,
        title: str = Form(...),
        description: str = Form(default=""),
        project: str = Form(default="HMX"),
        module: str = Form(default=""),
        menu: str = Form(default=""),
        assignee: str = Form(default=""),
        priority: str = Form(default="normal"),
        required_checks: str = Form(default=""),
        target_branch: str = Form(default="Human-Resources"),
        source_branch: str = Form(default=""),
        commit_ref: str = Form(default=""),
        mr_url: str = Form(default=""),
        pipeline_url: str = Form(default=""),
        version: int = Form(...),
    ) -> RedirectResponse:
        _require_login(request)
        try:
            store.update_task(
                task_ref,
                actor="web-admin",
                title=title,
                description=description,
                project=project,
                module=module,
                menu=menu,
                assignee=assignee,
                priority=priority,
                required_checks=[
                    item.strip()
                    for line in required_checks.splitlines()
                    for item in line.split(",")
                    if item.strip()
                ],
                target_branch=target_branch,
                source_branch=source_branch,
                commit_ref=commit_ref,
                mr_url=mr_url,
                pipeline_url=pipeline_url,
                expected_version=version,
            )
        except KeyError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Task tidak ditemukan") from exc
        except (PermissionError, ValueError) as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return RedirectResponse(f"/pmt/tasks/{task_ref}", status_code=status.HTTP_303_SEE_OTHER)

    @router.post("/tasks/{task_ref}/status")
    def update_status(
        request: Request,
        task_ref: str,
        task_status: str = Form(...),
        note: str = Form(default=""),
    ) -> RedirectResponse:
        _require_login(request)
        try:
            store.admin_transition_task(task_ref, task_status, "web-admin", note)
        except KeyError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Task tidak ditemukan") from exc
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return RedirectResponse(
            f"/pmt/tasks/{task_ref}#activity", status_code=status.HTTP_303_SEE_OTHER
        )

    @router.post("/tasks/{task_ref}/criteria")
    def add_criterion(request: Request, task_ref: str, text: str = Form(...)) -> RedirectResponse:
        _require_login(request)
        try:
            store.add_acceptance_criterion(task_ref, text, "web-admin")
        except KeyError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Task tidak ditemukan") from exc
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return RedirectResponse(
            f"/pmt/tasks/{task_ref}#acceptance", status_code=status.HTTP_303_SEE_OTHER
        )

    @router.post("/tasks/{task_ref}/criteria/{criterion_id}/toggle")
    def toggle_criterion(request: Request, task_ref: str, criterion_id: str) -> RedirectResponse:
        _require_login(request)
        try:
            store.toggle_acceptance_criterion(task_ref, criterion_id, "web-admin")
        except KeyError as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, detail="Task/criterion tidak ditemukan"
            ) from exc
        return RedirectResponse(
            f"/pmt/tasks/{task_ref}#acceptance", status_code=status.HTTP_303_SEE_OTHER
        )

    @router.post("/tasks/{task_ref}/evidence")
    def add_evidence(
        request: Request,
        task_ref: str,
        evidence_type: str = Form(...),
        label: str = Form(default=""),
        url: str = Form(default=""),
        note: str = Form(default=""),
    ) -> RedirectResponse:
        _require_login(request)
        try:
            store.add_evidence(
                task_ref,
                evidence_type=evidence_type,
                label=label,
                url=url,
                note=note,
                actor="web-admin",
            )
        except KeyError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Task tidak ditemukan") from exc
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return RedirectResponse(
            f"/pmt/tasks/{task_ref}#evidence", status_code=status.HTTP_303_SEE_OTHER
        )

    return router
