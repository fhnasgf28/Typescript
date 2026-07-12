from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mcp_transfer_node.app import create_app
from mcp_transfer_node.auth import hash_token
from mcp_transfer_node.config import TransferSettings


@pytest.fixture
def settings(tmp_path: Path) -> TransferSettings:
    return TransferSettings(
        server_name="server-b",
        base_dir=tmp_path / "mcp-transfer",
        max_file_mb=50,
        public_url="https://server-b.clipperyt.online",
        web_admin_password="admin-password",
        session_secret="session-secret-with-more-than-32-chars",
    )


@pytest.fixture
def client(settings: TransferSettings) -> Iterator[TestClient]:
    settings.config_dir.mkdir(parents=True, exist_ok=True)
    (settings.config_dir / "peers.json").write_text(
        '{"allowedPeers":[{"name":"server-a","tokenHash":"'
        + hash_token("valid-token")
        + '","enabled":true,"scopes":["approval.execute"]}]}',
        encoding="utf-8",
    )
    with TestClient(create_app(settings), base_url="https://testserver") as test_client:
        yield test_client
