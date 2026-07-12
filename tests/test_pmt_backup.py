from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone

from mcp_transfer_node.pmt_backup import create_backup
from mcp_transfer_node.pmt_store import PmtStore, TaskInput


def test_online_backup_is_verified_and_rotated(settings, tmp_path):
    store = PmtStore(settings.pmt_db_path)
    store.initialize()
    store.create_task(TaskInput(title="Backup task"))
    backup_dir = tmp_path / "backups"
    start = datetime(2026, 7, 12, 10, 0, tzinfo=timezone.utc)

    first = create_backup(settings.pmt_db_path, backup_dir, retention=2, now=start)
    create_backup(
        settings.pmt_db_path,
        backup_dir,
        retention=2,
        now=start + timedelta(minutes=1),
    )
    latest = create_backup(
        settings.pmt_db_path,
        backup_dir,
        retention=2,
        now=start + timedelta(minutes=2),
    )

    backups = sorted(backup_dir.glob("pmt-*.sqlite3"))
    assert len(backups) == 2
    assert first["integrity"] == "ok"
    assert latest["integrity"] == "ok"
    assert latest["table_count"] > 0
    latest_path = backups[-1]
    digest = hashlib.sha256(latest_path.read_bytes()).hexdigest()
    assert latest_path.with_suffix(".sqlite3.sha256").read_text().startswith(digest)
    db = sqlite3.connect(latest_path)
    try:
        assert db.execute("SELECT title FROM tasks").fetchone()[0] == "Backup task"
    finally:
        db.close()
