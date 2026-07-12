from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from mcp_transfer_node.config import load_settings


def create_backup(
    db_path: Path,
    backup_dir: Path,
    *,
    retention: int = 14,
    now: datetime | None = None,
) -> dict[str, object]:
    if not db_path.is_file():
        raise FileNotFoundError(f"PMT database does not exist: {db_path}")
    retention = max(2, min(retention, 90))
    timestamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    backup_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(backup_dir, 0o700)
    final_path = backup_dir / f"pmt-{timestamp}.sqlite3"
    temp_path = backup_dir / f".{final_path.name}.tmp"
    checksum_path = final_path.with_suffix(final_path.suffix + ".sha256")
    checksum_temp = checksum_path.with_name(f".{checksum_path.name}.tmp")

    for path in (temp_path, checksum_temp):
        path.unlink(missing_ok=True)

    source = sqlite3.connect(db_path, timeout=30)
    destination = sqlite3.connect(temp_path)
    try:
        with destination:
            source.backup(destination)
        integrity = destination.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"backup integrity check failed: {integrity}")
        table_count = destination.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0]
    finally:
        destination.close()
        source.close()

    os.chmod(temp_path, 0o600)
    digest = hashlib.sha256(temp_path.read_bytes()).hexdigest()
    checksum_temp.write_text(f"{digest}  {final_path.name}\n", encoding="utf-8")
    os.chmod(checksum_temp, 0o600)
    temp_path.replace(final_path)
    checksum_temp.replace(checksum_path)

    backups = sorted(backup_dir.glob("pmt-*.sqlite3"), reverse=True)
    removed: list[str] = []
    for old_path in backups[retention:]:
        old_path.unlink(missing_ok=True)
        old_path.with_suffix(old_path.suffix + ".sha256").unlink(missing_ok=True)
        removed.append(old_path.name)

    return {
        "status": "ok",
        "backup": str(final_path),
        "sha256": digest,
        "integrity": integrity,
        "table_count": table_count,
        "removed": removed,
    }


def run() -> None:
    parser = argparse.ArgumentParser(description="Create and verify an online PMT SQLite backup")
    parser.add_argument("--retention", type=int, default=14)
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()
    settings = load_settings()
    backup_dir = args.backup_dir or (settings.base_dir / "backups")
    result = create_backup(
        settings.pmt_db_path,
        backup_dir,
        retention=args.retention,
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    run()
