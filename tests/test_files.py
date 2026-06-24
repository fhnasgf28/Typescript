from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from mcp_transfer_node.config import TransferSettings
from mcp_transfer_node.files import (
    build_stored_filename,
    ensure_runtime_dirs,
    sanitize_filename,
    sha256_file,
)


def test_sanitize_filename_strips_paths_and_keeps_extension() -> None:
    assert sanitize_filename("../../.ssh/id_rsa") == "id_rsa"
    assert sanitize_filename("/etc/passwd") == "passwd"
    assert sanitize_filename("my report!!.pdf") == "my-report.pdf"
    assert sanitize_filename("archive.tar.gz") == "archive.tar.gz"


def test_build_stored_filename_is_timestamp_source_and_safe_name() -> None:
    received_at = datetime(2026, 6, 24, 21, 5, 1, tzinfo=timezone.utc)

    stored = build_stored_filename(received_at, "server-a", "my report!!.pdf", "transfer_abc")

    assert stored == "2026-06-24T210501Z-server-a-my-report.pdf"


def test_sha256_file_hashes_binary_bytes(tmp_path: Path) -> None:
    file_path = tmp_path / "image.bin"
    file_path.write_bytes(b"\x00\x01\x02")

    assert sha256_file(file_path) == (
        "ae4b3280e56e2faf83f414a6e3dabe9d5fbe18976544c05fed121accb85b53fc"
    )


def test_ensure_runtime_dirs_creates_expected_tree(tmp_path: Path) -> None:
    settings = TransferSettings(
        server_name="server-a",
        base_dir=tmp_path,
        max_file_mb=50,
        public_url="https://server-a.clipperyt.online",
        web_admin_password="admin-password",
        session_secret="session-secret-with-more-than-32-chars",
    )

    ensure_runtime_dirs(settings)

    assert settings.inbox_dir.is_dir()
    assert settings.metadata_dir.is_dir()
    assert settings.config_dir.is_dir()
    assert settings.logs_dir.is_dir()
