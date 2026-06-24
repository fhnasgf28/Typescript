from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Final, Pattern

from mcp_transfer_node.config import TransferSettings

UNSAFE_FILENAME_CHARS: Final[Pattern[str]] = re.compile(r"[^A-Za-z0-9._-]+")
MULTIPLE_DASHES: Final[Pattern[str]] = re.compile(r"-+")
CHUNK_SIZE_BYTES: Final = 1024 * 1024
MAX_STORED_FILENAME_LENGTH: Final = 180


def ensure_runtime_dirs(settings: TransferSettings) -> None:
    for path in (settings.inbox_dir, settings.metadata_dir, settings.config_dir, settings.logs_dir):
        path.mkdir(parents=True, exist_ok=True)


def sanitize_filename(filename: str) -> str:
    name = Path(filename).name
    if name in {"", ".", ".."}:
        name = "uploaded-file"

    sanitized = UNSAFE_FILENAME_CHARS.sub("-", name).strip(".-")
    sanitized = MULTIPLE_DASHES.sub("-", sanitized).replace("-.", ".")
    return sanitized or "uploaded-file"


def sanitize_source(source: str) -> str:
    sanitized = UNSAFE_FILENAME_CHARS.sub("-", source).strip(".-")
    return MULTIPLE_DASHES.sub("-", sanitized) or "unknown"


def build_stored_filename(
    received_at: datetime,
    source: str,
    original_filename: str,
    transfer_id: str,
) -> str:
    timestamp = received_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    source_name = sanitize_source(source)
    safe_name = sanitize_filename(original_filename)
    candidate = f"{timestamp}-{source_name}-{safe_name}"
    if len(candidate) <= MAX_STORED_FILENAME_LENGTH:
        return candidate

    extension = "".join(Path(safe_name).suffixes)[-20:]
    stem = Path(safe_name).stem[:80]
    return f"{timestamp}-{source_name}-{stem}-{sanitize_source(transfer_id)}{extension}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(CHUNK_SIZE_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()
