from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_reports_writable_storage(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["serverName"] == "server-b"
    assert payload["data"]["inboxWritable"] is True


def test_upload_rejects_missing_token(client: TestClient) -> None:
    response = client.post(
        "/api/upload",
        files={"file": ("hello.txt", b"hello", "text/plain")},
        data={"note": "manual test"},
        headers={"X-Transfer-Source": "server-a"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_upload_rejects_wrong_token(client: TestClient) -> None:
    response = client.post(
        "/api/upload",
        files={"file": ("hello.txt", b"hello", "text/plain")},
        headers={"Authorization": "Bearer wrong-token", "X-Transfer-Source": "server-a"},
    )

    assert response.status_code == 401


def test_upload_accepts_binary_file_and_writes_metadata(client: TestClient) -> None:
    response = client.post(
        "/api/upload",
        files={"file": ("image.bin", b"\x00\x01\x02", "application/octet-stream")},
        data={"note": "binary payload"},
        headers={"Authorization": "Bearer valid-token", "X-Transfer-Source": "server-a"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["transferId"].startswith("transfer_")
    assert payload["data"]["storedFilename"].endswith("server-a-image.bin")
    assert payload["data"]["sha256"] == (
        "ae4b3280e56e2faf83f414a6e3dabe9d5fbe18976544c05fed121accb85b53fc"
    )


def test_list_download_and_delete_uploaded_file(client: TestClient) -> None:
    upload = client.post(
        "/api/upload",
        files={"file": ("hello.txt", b"hello", "text/plain")},
        headers={"Authorization": "Bearer valid-token", "X-Transfer-Source": "server-a"},
    )
    transfer_id = upload.json()["data"]["transferId"]

    listing = client.get(
        "/api/files",
        headers={"Authorization": "Bearer valid-token", "X-Transfer-Source": "server-a"},
    )
    assert listing.status_code == 200
    assert listing.json()["data"]["files"][0]["id"] == transfer_id

    download = client.get(
        f"/api/files/{transfer_id}/download",
        headers={"Authorization": "Bearer valid-token", "X-Transfer-Source": "server-a"},
    )
    assert download.status_code == 200
    assert download.content == b"hello"

    delete = client.delete(
        f"/api/files/{transfer_id}",
        headers={"Authorization": "Bearer valid-token", "X-Transfer-Source": "server-a"},
    )
    assert delete.status_code == 200
    assert delete.json()["data"]["deleted"] is True
