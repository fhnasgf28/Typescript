from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

from mcp_transfer_node.config import TransferSettings, load_destinations, load_settings
from mcp_transfer_node.metadata import TransferRecord, get_record, list_records, mark_deleted

mcp = FastMCP("mcp-transfer-node")


@dataclass(frozen=True, slots=True)
class ResolvedDestination:
    name: str
    url: str
    token: str


def _metadata_path(settings: TransferSettings) -> Path:
    return settings.metadata_dir / "transfers.jsonl"


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


def resolve_destination(
    name: str,
    settings: TransferSettings,
    env: Mapping[str, str] | None = None,
) -> ResolvedDestination:
    source = os.environ if env is None else env
    for destination in load_destinations(settings.config_dir / "destinations.json"):
        if destination.name == name:
            token = source.get(destination.token_env)
            if not token:
                raise ValueError(
                    f"missing token env for destination {name}: {destination.token_env}",
                )
            return ResolvedDestination(destination.name, destination.url.rstrip("/"), token)

    if name.startswith("https://"):
        raise ValueError(
            "direct URL destinations require configured aliases to avoid exposing tokens"
        )
    raise ValueError(f"unknown destination: {name}")


async def send_file_to_destination(
    local_path: Path,
    destination: str,
    note: str,
    settings: TransferSettings,
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    if not local_path.exists():
        raise FileNotFoundError(f"local file not found: {local_path}")
    if not local_path.is_file():
        raise ValueError(f"local path is not a regular file: {local_path}")

    size_bytes = local_path.stat().st_size
    max_bytes = settings.max_file_mb * 1024 * 1024
    if size_bytes > max_bytes:
        raise ValueError(f"file exceeds {settings.max_file_mb} MB limit: {size_bytes} bytes")

    resolved = resolve_destination(destination, settings, env)
    headers = {
        "Authorization": f"Bearer {resolved.token}",
        "X-Transfer-Source": settings.server_name,
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        with local_path.open("rb") as file_obj:
            response = await client.post(
                f"{resolved.url}/api/upload",
                files={"file": (local_path.name, file_obj, "application/octet-stream")},
                data={"note": note},
                headers=headers,
            )

    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("destination returned an invalid upload response")

    data = payload.get("data")
    if (
        response.status_code >= 400
        or payload.get("success") is not True
        or not isinstance(data, dict)
    ):
        error = payload.get("error")
        message = (
            error.get("message", "unknown error") if isinstance(error, dict) else "unknown error"
        )
        raise RuntimeError(f"destination rejected upload: {message}")
    return dict(data)


@mcp.tool()
async def send_file(local_path: str, destination: str, note: str = "") -> dict[str, object]:
    return await send_file_to_destination(Path(local_path), destination, note, load_settings())


@mcp.tool()
def list_received_files(limit: int = 20) -> dict[str, object]:
    settings = load_settings()
    return {
        "files": [
            _record_to_dict(record)
            for record in list_records(_metadata_path(settings), limit)
            if record.status != "deleted"
        ],
    }


@mcp.tool()
def get_received_file_info(transfer_id: str) -> dict[str, object]:
    settings = load_settings()
    record = get_record(_metadata_path(settings), transfer_id)
    if record is None:
        raise ValueError(f"transfer not found: {transfer_id}")
    return _record_to_dict(record)


@mcp.tool()
def delete_received_file(transfer_id: str) -> dict[str, object]:
    settings = load_settings()
    record = get_record(_metadata_path(settings), transfer_id)
    if record is None:
        raise ValueError(f"transfer not found: {transfer_id}")

    path = Path(record.stored_path)
    if path.exists():
        path.unlink()
    return {
        "deleted": mark_deleted(_metadata_path(settings), transfer_id),
        "transferId": transfer_id,
    }


def run() -> None:
    mcp.run()
