from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from mcp_transfer_node.auth import verify_web_password
from mcp_transfer_node.config import TransferSettings
from mcp_transfer_node.files import CHUNK_SIZE_BYTES, build_stored_filename, sha256_file
from mcp_transfer_node.metadata import (
    TransferRecord,
    append_record,
    get_record,
    list_records,
    mark_deleted,
)

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _metadata_path(settings: TransferSettings) -> Path:
    return settings.metadata_dir / "transfers.jsonl"


def _require_login(request: Request) -> None:
    if request.session.get("authenticated") is not True:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )


def create_web_router(settings: TransferSettings) -> APIRouter:
    router = APIRouter()

    @router.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        return TEMPLATES.TemplateResponse(request, "login.html", {"error": ""})

    @router.post("/login", response_class=HTMLResponse)
    def login(request: Request, password: str = Form(...)):
        if not verify_web_password(password, settings.web_admin_password):
            return TEMPLATES.TemplateResponse(
                request,
                "login.html",
                {"error": "Login gagal"},
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        request.session["authenticated"] = True
        return RedirectResponse("/pmt", status_code=status.HTTP_303_SEE_OTHER)

    @router.post("/logout")
    def logout(request: Request) -> RedirectResponse:
        request.session.clear()
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    @router.get("/")
    def home(request: Request) -> RedirectResponse:
        _require_login(request)
        return RedirectResponse("/pmt", status_code=status.HTTP_303_SEE_OTHER)

    @router.get("/transfer", response_class=HTMLResponse)
    def index(request: Request):
        _require_login(request)
        records = [
            record
            for record in list_records(_metadata_path(settings))
            if record.status != "deleted"
        ]
        return TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {"settings": settings, "records": records},
        )

    @router.post("/web/upload")
    async def web_upload(
        request: Request,
        file: UploadFile = File(...),
        source: str = Form(default="manual"),
        note: str = Form(default=""),
    ) -> RedirectResponse:
        _require_login(request)
        received_at = datetime.now(timezone.utc)
        transfer_id = f"transfer_{uuid.uuid4().hex}"
        original_filename = file.filename or "uploaded-file"
        source_name = source or "manual"
        stored_filename = build_stored_filename(
            received_at,
            source_name,
            original_filename,
            transfer_id,
        )
        final_path = settings.inbox_dir / stored_filename
        temp_path = settings.inbox_dir / f".{transfer_id}.uploading"
        max_bytes = settings.max_file_mb * 1024 * 1024
        size_bytes = 0

        try:
            with temp_path.open("wb") as output:
                while chunk := await file.read(CHUNK_SIZE_BYTES):
                    size_bytes += len(chunk)
                    if size_bytes > max_bytes:
                        raise HTTPException(
                            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                            detail="File terlalu besar",
                        )
                    output.write(chunk)

            digest = sha256_file(temp_path)
            temp_path.rename(final_path)
            append_record(
                _metadata_path(settings),
                TransferRecord(
                    id=transfer_id,
                    received_at=received_at,
                    source=source_name,
                    original_filename=original_filename,
                    stored_filename=stored_filename,
                    stored_path=str(final_path),
                    size_bytes=size_bytes,
                    sha256=digest,
                    note=note,
                    status="received",
                ),
            )
            return RedirectResponse("/transfer", status_code=status.HTTP_303_SEE_OTHER)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    @router.get("/web/files/{transfer_id}/download")
    def web_download(request: Request, transfer_id: str) -> FileResponse:
        _require_login(request)
        record = get_record(_metadata_path(settings), transfer_id)
        if record is None or record.status == "deleted":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File tidak ditemukan",
            )

        path = Path(record.stored_path)
        if not path.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File tidak ditemukan",
            )
        return FileResponse(path, filename=record.original_filename)

    @router.post("/web/files/{transfer_id}/delete")
    def web_delete(request: Request, transfer_id: str) -> RedirectResponse:
        _require_login(request)
        record = get_record(_metadata_path(settings), transfer_id)
        if record is not None:
            path = Path(record.stored_path)
            if path.exists():
                path.unlink()
            mark_deleted(_metadata_path(settings), transfer_id)
        return RedirectResponse("/transfer", status_code=status.HTTP_303_SEE_OTHER)

    return router
