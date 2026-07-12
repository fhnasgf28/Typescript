from __future__ import annotations

import json
import secrets
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from mcp_transfer_node.config import TransferSettings
from mcp_transfer_node.pmt_context import GoogleDocsContextService, GoogleDocsFetcher
from mcp_transfer_node.pmt_gdocs import read_google_doc
from mcp_transfer_node.pmt_sheet import validate_sheet_url
from mcp_transfer_node.pmt_store import (
    ADMIN_STATUS_TRANSITIONS,
    APPROVAL_ACTION_TYPES,
    APPROVAL_STATUSES,
    EVIDENCE_TYPES,
    PmtStore,
    TaskInput,
)

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
MAX_WEB_CONTEXT_CHARS_PER_DOCUMENT = 20_000
MAX_WEB_CONTEXT_CHARS_PER_TAB = 5_000


def _bounded_web_context(document: dict[str, object]) -> dict[str, object]:
    """Bound untrusted document text rendered into the server-side task page."""
    remaining = MAX_WEB_CONTEXT_CHARS_PER_DOCUMENT
    bounded_tabs: list[dict[str, object]] = []
    for raw_tab in document.get("tabs", []):
        tab = dict(raw_tab)
        text = str(tab.pop("text", ""))
        allowance = min(MAX_WEB_CONTEXT_CHARS_PER_TAB, remaining)
        display_text = text[:allowance]
        remaining -= len(display_text)
        tab.pop("paragraphs", None)
        tab.pop("tables", None)
        tab["display_text"] = display_text
        tab["display_truncated"] = len(display_text) < len(text)
        bounded_tabs.append(tab)
    return {**document, "tabs": bounded_tabs}


def _require_login(request: Request) -> None:
    if request.session.get("authenticated") is not True or not request.session.get("principal"):
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )


def _principal(request: Request) -> str:
    _require_login(request)
    return str(request.session["principal"])


def _require_csrf(request: Request, token: str) -> None:
    expected = str(request.session.get("csrf_token", ""))
    if not expected or not secrets.compare_digest(expected, token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="CSRF token tidak valid")


def create_pmt_web_router(
    settings: TransferSettings,
    *,
    google_docs_fetcher: GoogleDocsFetcher = read_google_doc,
) -> APIRouter:
    store = PmtStore(settings.pmt_db_path)
    store.initialize()
    context_service = GoogleDocsContextService(store, settings, fetcher=google_docs_fetcher)
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
                "csrf_token": request.session["csrf_token"],
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
                "csrf_token": request.session["csrf_token"],
            },
        )

    @router.post("/agents/{agent_id}/mode")
    def update_agent_mode(
        request: Request,
        agent_id: str,
        mode: str = Form(...),
        csrf_token: str = Form(...),
    ) -> RedirectResponse:
        _require_login(request)
        _require_csrf(request, csrf_token)
        try:
            store.set_agent_mode(agent_id, mode, _principal(request))
        except KeyError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Agent tidak ditemukan") from exc
        except ValueError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return RedirectResponse("/pmt/agents", status_code=status.HTTP_303_SEE_OTHER)

    @router.post("/agents/reconcile-leases")
    def reconcile_agent_leases(request: Request, csrf_token: str = Form(...)) -> RedirectResponse:
        _require_login(request)
        _require_csrf(request, csrf_token)
        store.reconcile_expired_leases(_principal(request))
        store.reconcile_expired_approval_leases(_principal(request))
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
                "csrf_token": request.session["csrf_token"],
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
        csrf_token: str = Form(...),
    ) -> RedirectResponse:
        _require_login(request)
        _require_csrf(request, csrf_token)
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
                _principal(request),
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return RedirectResponse("/pmt/sync", status_code=status.HTTP_303_SEE_OTHER)

    @router.post("/sync/schedules/{schedule_id}/toggle")
    def toggle_sync_schedule(
        request: Request,
        schedule_id: str,
        enabled: bool = Form(...),
        csrf_token: str = Form(...),
    ) -> RedirectResponse:
        _require_login(request)
        _require_csrf(request, csrf_token)
        try:
            store.set_schedule_enabled(schedule_id, enabled)
        except KeyError as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, detail="Schedule tidak ditemukan"
            ) from exc
        return RedirectResponse("/pmt/sync", status_code=status.HTTP_303_SEE_OTHER)

    @router.get("/approvals", response_class=HTMLResponse)
    def approval_center(request: Request, approval_status: str | None = None):
        _require_login(request)
        try:
            approvals = store.list_approvals(status=approval_status, limit=200)
        except ValueError:
            approvals = store.list_approvals(limit=200)
        return TEMPLATES.TemplateResponse(
            request,
            "pmt_approvals.html",
            {
                "settings": settings,
                "approvals": approvals,
                "selected": None,
                "events": [],
                "runs": [],
                "action_types": sorted(APPROVAL_ACTION_TYPES),
                "approval_statuses": sorted(APPROVAL_STATUSES),
                "csrf_token": request.session["csrf_token"],
                "principal": _principal(request),
            },
        )

    @router.get("/approvals/{approval_ref}", response_class=HTMLResponse)
    def approval_detail(request: Request, approval_ref: str):
        _require_login(request)
        approval = store.get_approval(approval_ref)
        if approval is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Approval tidak ditemukan")
        return TEMPLATES.TemplateResponse(
            request,
            "pmt_approvals.html",
            {
                "settings": settings,
                "approvals": store.list_approvals(limit=200),
                "selected": approval,
                "events": store.approval_events(approval_ref),
                "runs": store.list_approval_runs(approval_ref),
                "action_types": sorted(APPROVAL_ACTION_TYPES),
                "approval_statuses": sorted(APPROVAL_STATUSES),
                "csrf_token": request.session["csrf_token"],
                "principal": _principal(request),
            },
        )

    @router.post("/approvals")
    def create_approval(
        request: Request,
        csrf_token: str = Form(...),
        action_type: str = Form(...),
        title: str = Form(...),
        reason: str = Form(default=""),
        payload_json: str = Form(...),
        idempotency_key: str = Form(...),
        task_ref: str = Form(default=""),
    ) -> RedirectResponse:
        _require_login(request)
        _require_csrf(request, csrf_token)
        try:
            payload = json.loads(payload_json)
            if not isinstance(payload, dict):
                raise ValueError("payload harus berupa JSON object")
            approval = store.create_approval_request(
                action_type=action_type,
                title=title,
                reason=reason,
                payload=payload,
                requested_by=_principal(request),
                idempotency_key=idempotency_key,
                task_ref=task_ref or None,
                admin_request=True,
            )
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail="Payload JSON tidak valid"
            ) from exc
        except (KeyError, PermissionError, ValueError) as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return RedirectResponse(
            f"/pmt/approvals/{approval['approval_key']}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    @router.post("/approvals/{approval_ref}/decision")
    def decide_approval(
        request: Request,
        approval_ref: str,
        csrf_token: str = Form(...),
        decision: str = Form(...),
        note: str = Form(default=""),
        confirm_key: str = Form(...),
        version: int = Form(...),
        approval_ttl_minutes: int = Form(default=60),
    ) -> RedirectResponse:
        _require_login(request)
        _require_csrf(request, csrf_token)
        approval = store.get_approval(approval_ref)
        if approval is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Approval tidak ditemukan")
        if not secrets.compare_digest(confirm_key.strip(), approval["approval_key"]):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail="Ketik approval key secara tepat untuk mengonfirmasi keputusan",
            )
        try:
            store.decide_approval(
                approval_ref,
                decision,
                _principal(request),
                note,
                version,
                max(5, approval_ttl_minutes) * 60,
            )
        except KeyError as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, detail="Approval tidak ditemukan"
            ) from exc
        except (PermissionError, ValueError) as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return RedirectResponse(
            f"/pmt/approvals/{approval['approval_key']}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

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
        csrf_token: str = Form(...),
    ) -> RedirectResponse:
        _require_login(request)
        _require_csrf(request, csrf_token)
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
                actor=_principal(request),
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
                "context_documents": [
                    _bounded_web_context(store.get_task_context_document(task_ref, item["id"]))
                    for item in store.list_task_context_documents(task_ref)
                ],
                "google_docs_configured": settings.google_docs_service_account_file is not None,
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
                "csrf_token": request.session["csrf_token"],
            },
        )

    @router.post("/tasks/{task_ref}/context")
    async def attach_context(
        request: Request,
        task_ref: str,
        source_url: str = Form(...),
        version: int = Form(...),
        csrf_token: str = Form(...),
    ) -> RedirectResponse:
        _require_login(request)
        _require_csrf(request, csrf_token)
        try:
            await context_service.attach(
                task_ref,
                source_url,
                actor=_principal(request),
                expected_version=version,
            )
        except KeyError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Task tidak ditemukan") from exc
        except (PermissionError, ValueError) as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return RedirectResponse(
            f"/pmt/tasks/{task_ref}#external-context", status_code=status.HTTP_303_SEE_OTHER
        )

    @router.post("/tasks/{task_ref}/context/{context_ref}/refresh")
    async def refresh_context(
        request: Request,
        task_ref: str,
        context_ref: str,
        version: int = Form(...),
        context_version: int = Form(...),
        csrf_token: str = Form(...),
    ) -> RedirectResponse:
        _require_login(request)
        _require_csrf(request, csrf_token)
        try:
            await context_service.refresh(
                task_ref,
                context_ref,
                actor=_principal(request),
                expected_version=version,
                expected_context_version=context_version,
            )
        except KeyError as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, detail="Context tidak ditemukan"
            ) from exc
        except (PermissionError, ValueError) as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return RedirectResponse(
            f"/pmt/tasks/{task_ref}#external-context", status_code=status.HTTP_303_SEE_OTHER
        )

    @router.post("/tasks/{task_ref}/context/{context_ref}/remove")
    def remove_context(
        request: Request,
        task_ref: str,
        context_ref: str,
        version: int = Form(...),
        context_version: int = Form(...),
        csrf_token: str = Form(...),
    ) -> RedirectResponse:
        _require_login(request)
        _require_csrf(request, csrf_token)
        try:
            store.remove_task_context_document(
                task_ref,
                context_ref,
                actor=_principal(request),
                expected_version=version,
                expected_context_version=context_version,
            )
        except KeyError as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, detail="Context tidak ditemukan"
            ) from exc
        except (PermissionError, ValueError) as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return RedirectResponse(
            f"/pmt/tasks/{task_ref}#external-context", status_code=status.HTTP_303_SEE_OTHER
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
        csrf_token: str = Form(...),
    ) -> RedirectResponse:
        _require_login(request)
        _require_csrf(request, csrf_token)
        try:
            store.update_task(
                task_ref,
                actor=_principal(request),
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
        version: int = Form(...),
        csrf_token: str = Form(...),
    ) -> RedirectResponse:
        _require_login(request)
        _require_csrf(request, csrf_token)
        try:
            store.admin_transition_task(
                task_ref, task_status, _principal(request), note, expected_version=version
            )
        except KeyError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Task tidak ditemukan") from exc
        except (PermissionError, ValueError) as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return RedirectResponse(
            f"/pmt/tasks/{task_ref}#activity", status_code=status.HTTP_303_SEE_OTHER
        )

    @router.post("/tasks/{task_ref}/criteria")
    def add_criterion(
        request: Request,
        task_ref: str,
        text: str = Form(...),
        version: int = Form(...),
        csrf_token: str = Form(...),
    ) -> RedirectResponse:
        _require_login(request)
        _require_csrf(request, csrf_token)
        try:
            store.add_acceptance_criterion(
                task_ref, text, _principal(request), expected_version=version
            )
        except KeyError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Task tidak ditemukan") from exc
        except (PermissionError, ValueError) as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return RedirectResponse(
            f"/pmt/tasks/{task_ref}#acceptance", status_code=status.HTTP_303_SEE_OTHER
        )

    @router.post("/tasks/{task_ref}/criteria/{criterion_id}/toggle")
    def toggle_criterion(
        request: Request,
        task_ref: str,
        criterion_id: str,
        version: int = Form(...),
        csrf_token: str = Form(...),
    ) -> RedirectResponse:
        _require_login(request)
        _require_csrf(request, csrf_token)
        try:
            store.toggle_acceptance_criterion(
                task_ref, criterion_id, _principal(request), expected_version=version
            )
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
        version: int = Form(...),
        csrf_token: str = Form(...),
    ) -> RedirectResponse:
        _require_login(request)
        _require_csrf(request, csrf_token)
        try:
            store.add_evidence(
                task_ref,
                evidence_type=evidence_type,
                label=label,
                url=url,
                note=note,
                actor=_principal(request),
                expected_version=version,
            )
        except KeyError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Task tidak ditemukan") from exc
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return RedirectResponse(
            f"/pmt/tasks/{task_ref}#evidence", status_code=status.HTTP_303_SEE_OTHER
        )

    return router
