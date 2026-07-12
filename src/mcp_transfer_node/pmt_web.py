from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from mcp_transfer_node.config import TransferSettings
from mcp_transfer_node.pmt_store import PmtStore, TaskInput

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
        return TEMPLATES.TemplateResponse(
            request,
            "pmt_dashboard.html",
            {"settings": settings, "tasks": tasks, "grouped": grouped},
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

    return router
