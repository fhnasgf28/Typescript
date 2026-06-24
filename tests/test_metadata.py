from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from mcp_transfer_node.metadata import (
    TransferRecord,
    append_record,
    get_record,
    list_records,
    mark_deleted,
)


def make_record(transfer_id: str = "transfer_abc") -> TransferRecord:
    return TransferRecord(
        id=transfer_id,
        received_at=datetime(2026, 6, 24, 21, 5, 1, tzinfo=timezone.utc),
        source="server-a",
        original_filename="report.txt",
        stored_filename="2026-06-24T210501Z-server-a-report.txt",
        stored_path="/home/fhnasgf/mcp-transfer/inbox/2026-06-24T210501Z-server-a-report.txt",
        size_bytes=12,
        sha256="abc123",
        note="report terbaru",
        status="received",
    )


def test_append_and_list_records(tmp_path: Path) -> None:
    metadata_path = tmp_path / "transfers.jsonl"
    append_record(metadata_path, make_record())

    records = list_records(metadata_path)

    assert len(records) == 1
    assert records[0].id == "transfer_abc"
    assert records[0].source == "server-a"


def test_get_record_returns_matching_transfer(tmp_path: Path) -> None:
    metadata_path = tmp_path / "transfers.jsonl"
    append_record(metadata_path, make_record("transfer_1"))
    append_record(metadata_path, make_record("transfer_2"))

    record = get_record(metadata_path, "transfer_2")

    assert record is not None
    assert record.id == "transfer_2"


def test_mark_deleted_updates_record_status(tmp_path: Path) -> None:
    metadata_path = tmp_path / "transfers.jsonl"
    append_record(metadata_path, make_record())

    changed = mark_deleted(metadata_path, "transfer_abc")

    assert changed is True
    record = get_record(metadata_path, "transfer_abc")
    assert record is not None
    assert record.status == "deleted"
