from __future__ import annotations

from fastapi.testclient import TestClient


def test_login_page_loads(client: TestClient) -> None:
    response = client.get("/login")

    assert response.status_code == 200
    assert "Standalone PMT" in response.text
    assert "bootstrap@5.3.3" in response.text
    assert "/static/pmt.css" in response.text


def test_pmt_stylesheet_is_served(client: TestClient) -> None:
    response = client.get("/static/pmt.css")

    assert response.status_code == 200
    assert "pmt-kanban" in response.text
    assert response.headers["content-type"].startswith("text/css")


def test_index_requires_login(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_wrong_password_stays_on_login(client: TestClient) -> None:
    response = client.post("/login", data={"password": "wrong"})

    assert response.status_code == 401
    assert "Login gagal" in response.text


def test_correct_password_opens_inbox(client: TestClient) -> None:
    response = client.post("/login", data={"password": "admin-password"}, follow_redirects=True)

    assert response.status_code == 200
    assert "MCP Transfer Node - server-b" in response.text


def test_web_upload_lists_and_deletes_file(client: TestClient) -> None:
    client.post("/login", data={"password": "admin-password"})

    upload = client.post(
        "/web/upload",
        files={"file": ("manual.pdf", b"pdf-bytes", "application/pdf")},
        data={"source": "manual", "note": "from browser"},
        follow_redirects=True,
    )
    assert upload.status_code == 200
    assert "manual.pdf" in upload.text

    transfer_id = upload.text.split('data-transfer-id="')[1].split('"')[0]
    download = client.get(f"/web/files/{transfer_id}/download")
    assert download.status_code == 200
    assert download.content == b"pdf-bytes"

    delete = client.post(f"/web/files/{transfer_id}/delete", follow_redirects=True)
    assert delete.status_code == 200
    assert "manual.pdf" not in delete.text
