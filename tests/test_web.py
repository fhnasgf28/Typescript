from __future__ import annotations

import re

from fastapi.testclient import TestClient


def _login_and_csrf(client: TestClient, path: str) -> str:
    login = client.post("/login", data={"username": "admin", "password": "admin-password"})
    assert login.status_code == 200
    page = client.get(path)
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert match is not None
    return match.group(1)


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
    response = client.post("/login", data={"username": "admin", "password": "wrong"})

    assert response.status_code == 401
    assert "Login gagal" in response.text


def test_wrong_username_stays_on_login(client: TestClient) -> None:
    response = client.post(
        "/login", data={"username": "someone-else", "password": "admin-password"}
    )

    assert response.status_code == 401
    assert "Login gagal" in response.text


def test_correct_password_opens_pmt_dashboard(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"username": "admin", "password": "admin-password"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert response.url.path == "/pmt"
    assert "Task Dashboard" in response.text
    session_cookie = response.history[0].headers["set-cookie"].lower()
    assert "httponly" in session_cookie
    assert "samesite=strict" in session_cookie
    assert "secure" in session_cookie


def test_authenticated_root_redirects_to_pmt(client: TestClient) -> None:
    client.post("/login", data={"username": "admin", "password": "admin-password"})

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/pmt"


def test_transfer_inbox_remains_available(client: TestClient) -> None:
    client.post("/login", data={"username": "admin", "password": "admin-password"})

    response = client.get("/transfer")

    assert response.status_code == 200
    assert "MCP Transfer Node - server-b" in response.text


def test_web_upload_lists_deletes_and_logs_out(client: TestClient) -> None:
    csrf = _login_and_csrf(client, "/pmt")

    upload = client.post(
        "/web/upload",
        files={"file": ("manual.pdf", b"pdf-bytes", "application/pdf")},
        data={"source": "manual", "note": "from browser", "csrf_token": csrf},
        follow_redirects=True,
    )
    assert upload.status_code == 200
    assert "manual.pdf" in upload.text

    transfer_id = upload.text.split('data-transfer-id="')[1].split('"')[0]
    download = client.get(f"/web/files/{transfer_id}/download")
    assert download.status_code == 200
    assert download.content == b"pdf-bytes"

    delete = client.post(
        f"/web/files/{transfer_id}/delete",
        data={"csrf_token": csrf},
        follow_redirects=True,
    )
    assert delete.status_code == 200
    assert "manual.pdf" not in delete.text

    logout = client.post("/logout", data={"csrf_token": csrf}, follow_redirects=False)
    assert logout.status_code == 303
    assert logout.headers["location"] == "/login"
    assert client.get("/transfer", follow_redirects=False).status_code == 303
