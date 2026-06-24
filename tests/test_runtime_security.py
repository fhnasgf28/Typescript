from __future__ import annotations

from fastapi.testclient import TestClient


def test_path_traversal_filename_never_writes_outside_inbox(client: TestClient) -> None:
    response = client.post(
        "/api/upload",
        files={"file": ("../../.ssh/id_rsa", b"secret-looking-bytes", "application/octet-stream")},
        headers={"Authorization": "Bearer valid-token", "X-Transfer-Source": "server-a"},
    )

    assert response.status_code == 200
    stored_filename = response.json()["data"]["storedFilename"]
    assert ".." not in stored_filename
    assert "/" not in stored_filename
    assert stored_filename.endswith("server-a-id_rsa")


def test_api_error_does_not_echo_token(client: TestClient) -> None:
    response = client.post(
        "/api/upload",
        files={"file": ("hello.txt", b"hello", "text/plain")},
        headers={"Authorization": "Bearer leaked-token-value", "X-Transfer-Source": "server-a"},
    )

    body = response.text
    assert response.status_code == 401
    assert "leaked-token-value" not in body


def test_oversized_file_is_rejected(client: TestClient) -> None:
    too_large = b"x" * (51 * 1024 * 1024)

    response = client.post(
        "/api/upload",
        files={"file": ("big.bin", too_large, "application/octet-stream")},
        headers={"Authorization": "Bearer valid-token", "X-Transfer-Source": "server-a"},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"
