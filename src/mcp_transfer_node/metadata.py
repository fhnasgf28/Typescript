from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class TransferRecord:
    id: str
    received_at: datetime
    source: str
    original_filename: str
    stored_filename: str
    stored_path: str
    size_bytes: int
    sha256: str
    note: str
    status: str

    def to_json_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["received_at"] = self.received_at.isoformat()
        return payload

    @classmethod
    def from_json_dict(cls, payload: dict[str, object]) -> TransferRecord:
        return cls(
            id=str(payload["id"]),
            received_at=datetime.fromisoformat(str(payload["received_at"])),
            source=str(payload["source"]),
            original_filename=str(payload["original_filename"]),
            stored_filename=str(payload["stored_filename"]),
            stored_path=str(payload["stored_path"]),
            size_bytes=int(payload["size_bytes"]),
            sha256=str(payload["sha256"]),
            note=str(payload.get("note", "")),
            status=str(payload["status"]),
        )


def append_record(path: Path, record: TransferRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file_obj:
        file_obj.write(json.dumps(record.to_json_dict(), sort_keys=True) + "\n")


def list_records(path: Path, limit: int = 50) -> list[TransferRecord]:
    if not path.exists():
        return []

    records = [
        TransferRecord.from_json_dict(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return list(reversed(records))[:limit]


def get_record(path: Path, transfer_id: str) -> TransferRecord | None:
    return next((record for record in list_records(path, 10_000) if record.id == transfer_id), None)


def mark_deleted(path: Path, transfer_id: str) -> bool:
    if not path.exists():
        return False

    records = list(reversed(list_records(path, 10_000)))
    changed = False
    updated: list[TransferRecord] = []
    for record in records:
        if record.id == transfer_id and record.status != "deleted":
            updated.append(
                TransferRecord(
                    id=record.id,
                    received_at=record.received_at,
                    source=record.source,
                    original_filename=record.original_filename,
                    stored_filename=record.stored_filename,
                    stored_path=record.stored_path,
                    size_bytes=record.size_bytes,
                    sha256=record.sha256,
                    note=record.note,
                    status="deleted",
                ),
            )
            changed = True
        else:
            updated.append(record)

    if changed:
        path.write_text(
            "".join(json.dumps(record.to_json_dict(), sort_keys=True) + "\n" for record in updated),
            encoding="utf-8",
        )
    return changed
