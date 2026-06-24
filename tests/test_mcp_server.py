from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from mcp_transfer_node.config import TransferSettings
from mcp_transfer_node.mcp_server import resolve_destination, send_file_to_destination


def test_resolve_destination_loads_url_and_token_from_env(tmp_path: Path) -> None:
    settings = TransferSettings(
        server_name="server-a",
        base_dir=tmp_path,
        max_file_mb=50,
        public_url="https://server-a.clipperyt.online",
        web_admin_password="admin-password",
        session_secret="session-secret-with-more-than-32-chars",
    )
    settings.config_dir.mkdir(parents=True)
    (settings.config_dir / "destinations.json").write_text(
        '{"destinations":[{"name":"server-b","url":"https://server-b.clipperyt.online",'
        '"tokenEnv":"MCP_TRANSFER_DEST_SERVER_B_TOKEN"}]}',
        encoding="utf-8",
    )

    destination = resolve_destination(
        "server-b",
        settings,
        {"MCP_TRANSFER_DEST_SERVER_B_TOKEN": "secret-token"},
    )

    assert destination.name == "server-b"
    assert destination.url == "https://server-b.clipperyt.online"
    assert destination.token == "secret-token"


@pytest.mark.asyncio
async def test_send_file_to_destination_uploads_bytes_with_headers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = TransferSettings(
        server_name="server-a",
        base_dir=tmp_path,
        max_file_mb=50,
        public_url="https://server-a.clipperyt.online",
        web_admin_password="admin-password",
        session_secret="session-secret-with-more-than-32-chars",
    )
    settings.config_dir.mkdir(parents=True)
    (settings.config_dir / "destinations.json").write_text(
        '{"destinations":[{"name":"server-b","url":"https://server-b.clipperyt.online",'
        '"tokenEnv":"MCP_TRANSFER_DEST_SERVER_B_TOKEN"}]}',
        encoding="utf-8",
    )
    local_file = tmp_path / "report.txt"
    local_file.write_text("hello", encoding="utf-8")
    captured: dict[str, object] = {}

    async def fake_post(
        self: httpx.AsyncClient,
        url: str,
        files: dict[str, tuple[str, object, str]],
        data: dict[str, str],
        headers: dict[str, str],
    ) -> httpx.Response:
        captured["url"] = url
        captured["headers"] = headers
        captured["data"] = data
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "transferId": "transfer_abc",
                    "storedFilename": "stored-report.txt",
                    "sha256": "abc123",
                },
                "error": None,
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    result = await send_file_to_destination(
        local_file,
        "server-b",
        "report terbaru",
        settings,
        {"MCP_TRANSFER_DEST_SERVER_B_TOKEN": "secret-token"},
    )

    assert captured["url"] == "https://server-b.clipperyt.online/api/upload"
    assert captured["headers"] == {
        "Authorization": "Bearer secret-token",
        "X-Transfer-Source": "server-a",
    }
    assert captured["data"] == {"note": "report terbaru"}
    assert result["transferId"] == "transfer_abc"
