from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from mcp_transfer_node.auth import authenticate_peer
from mcp_transfer_node.config import AllowedPeer, TransferSettings, load_allowed_peers
from mcp_transfer_node.files import (
    CHUNK_SIZE_BYTES,
    build_stored_filename,
    ensure_runtime_dirs,
    sha256_file,
)
from mcp_transfer_node.metadata import (
    TransferRecord,
    append_record,
    get_record,
    list_records,
    mark_deleted,
)
from mcp_transfer_node.responses import error_response, success_response

logger = logging.getLogger(__name__)


def _metadata_path(settings: TransferSettings) -> Path:
    return settings.metadata_dir / "transfers.jsonl"


def _peers(settings: TransferSettings) -> list[AllowedPeer]:
    return load_allowed_peers(settings.config_dir / "peers.json")


def _record_to_dict(record: TransferRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "receivedAt": record.received_at.isoformat(),
        "source": record.source,
        "originalFilename": record.original_filename,
        "storedFilename": record.stored_filename,
        "storedPath": record.stored_path,
        "sizeBytes": record.size_bytes,
        "sha256": record.sha256,
        "note": record.note,
        "status": record.status,
    }


def create_api_router(settings: TransferSettings) -> APIRouter:
    ensure_runtime_dirs(settings)
    router = APIRouter()

    def require_peer(
        authorization: str | None = Header(default=None),
        x_transfer_source: str | None = Header(default=None),
    ) -> AllowedPeer:
        if not authorization or not authorization.startswith("Bearer ") or not x_transfer_source:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=error_response("UNAUTHORIZED", "Invalid or missing bearer token"),
            )

        peer = authenticate_peer(
            authorization.removeprefix("Bearer ").strip(),
            x_transfer_source,
            _peers(settings),
        )
        if peer is None:
            logger.warning(
                "upload rejected source=%s reason=invalid_credentials", x_transfer_source
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=error_response("UNAUTHORIZED", "Invalid or missing bearer token"),
            )
        return peer

    @router.get("/health")
    def health() -> dict[str, object]:
        ensure_runtime_dirs(settings)
        return success_response(
            {
                "serverName": settings.server_name,
                "status": "ok",
                "inboxWritable": os.access(settings.inbox_dir, os.W_OK),
                "metadataWritable": os.access(settings.metadata_dir, os.W_OK),
                "maxFileMb": settings.max_file_mb,
            },
        )

    @router.post("/api/upload")
    async def upload_file(
        peer: AllowedPeer = Depends(require_peer),
        file: UploadFile = File(...),
        note: str = Form(default=""),
    ) -> dict[str, object]:
        received_at = datetime.now(timezone.utc)
        transfer_id = f"transfer_{uuid.uuid4().hex}"
        original_filename = file.filename or "uploaded-file"
        stored_filename = build_stored_filename(
            received_at,
            peer.name,
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
                            detail=error_response(
                                "FILE_TOO_LARGE",
                                f"File exceeds {settings.max_file_mb} MB limit",
                            ),
                        )
                    output.write(chunk)

            digest = sha256_file(temp_path)
            temp_path.rename(final_path)
            append_record(
                _metadata_path(settings),
                TransferRecord(
                    id=transfer_id,
                    received_at=received_at,
                    source=peer.name,
                    original_filename=original_filename,
                    stored_filename=stored_filename,
                    stored_path=str(final_path),
                    size_bytes=size_bytes,
                    sha256=digest,
                    note=note,
                    status="received",
                ),
            )
            return success_response(
                {"transferId": transfer_id, "storedFilename": stored_filename, "sha256": digest},
            )
        finally:
            if temp_path.exists():
                temp_path.unlink()

    @router.get("/api/files")
    def files(_: AllowedPeer = Depends(require_peer)) -> dict[str, object]:
        records = [
            _record_to_dict(record)
            for record in list_records(_metadata_path(settings))
            if record.status != "deleted"
        ]
        return success_response({"files": records})

    @router.get("/api/files/{transfer_id}/download")
    def download_file(transfer_id: str, _: AllowedPeer = Depends(require_peer)) -> FileResponse:
        record = get_record(_metadata_path(settings), transfer_id)
        if record is None or record.status == "deleted":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=error_response("NOT_FOUND", "Transfer not found"),
            )

        path = Path(record.stored_path)
        if not path.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=error_response("NOT_FOUND", "Transfer not found"),
            )
        return FileResponse(path, filename=record.original_filename)

    @router.delete("/api/files/{transfer_id}")
    def delete_file(transfer_id: str, _: AllowedPeer = Depends(require_peer)) -> dict[str, object]:
        record = get_record(_metadata_path(settings), transfer_id)
        if record is None or record.status == "deleted":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=error_response("NOT_FOUND", "Transfer not found"),
            )

        path = Path(record.stored_path)
        if path.exists():
            path.unlink()
        return success_response({"deleted": mark_deleted(_metadata_path(settings), transfer_id)})

    return router
