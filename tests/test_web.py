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


def test_google_docs_context_renders_safe_semantic_document_reader(client, settings) -> None:
    from mcp_transfer_node.pmt_gdocs import parse_google_doc_payload
    from mcp_transfer_node.pmt_store import PmtStore, TaskInput

    def paragraph(
        text: str,
        *,
        style: str = "NORMAL_TEXT",
        bullet: dict | None = None,
        link: str | None = None,
    ) -> dict:
        text_style = {"link": {"url": link}} if link else {}
        value = {
            "elements": [{"textRun": {"content": text + "\n", "textStyle": text_style}}],
            "paragraphStyle": {"namedStyleType": style},
        }
        if bullet is not None:
            value["bullet"] = bullet
        return {"paragraph": value}

    table = {
        "table": {
            "tableRows": [
                {
                    "tableCells": [
                        {"content": [paragraph("Role")]},
                        {"content": [paragraph("Access")]},
                    ]
                },
                {
                    "tableCells": [
                        {"content": [paragraph("Supervisor"), paragraph("Regional")]},
                        {"content": [paragraph("<script>alert(1)</script>")]},
                    ]
                },
            ]
        }
    }
    child = {
        "tabProperties": {
            "tabId": "child",
            "title": "Access Right Supervisor",
            "parentTabId": "root",
        },
        "documentTab": {"body": {"content": [paragraph("Selected content")]}},
        "childTabs": [],
    }
    payload = {
        "documentId": "doc123",
        "title": "Access Right Fixing",
        "revisionId": "revision-1",
        "tabs": [
            {
                "tabProperties": {"tabId": "root", "title": "Overview"},
                "documentTab": {
                    "body": {
                        "content": [
                            paragraph("Access matrix", style="HEADING_2"),
                            paragraph("Read the specification", link="https://example.test/spec"),
                            paragraph(
                                "Review supervisor access",
                                bullet={"listId": "list-1", "nestingLevel": 0},
                            ),
                            paragraph("Unsafe", link="javascript:alert(1)"),
                            table,
                        ]
                    }
                },
                "childTabs": [child],
            }
        ],
    }

    store = PmtStore(settings.pmt_db_path)
    store.initialize()
    task = store.create_task(TaskInput(title="Docs reader task"), actor="Farhan")
    snapshot = parse_google_doc_payload(payload, selected_tab_id="child")
    attached = store.save_task_context_snapshot(
        task["task_key"],
        source_url="https://docs.google.com/document/d/doc123/edit?tab=child",
        snapshot=snapshot,
        actor="Farhan",
        operation="attach",
        expected_version=task["version"],
    )

    client.post("/login", data={"username": "admin", "password": "admin-password"})
    response = client.get(f"/pmt/tasks/{task['task_key']}")

    assert response.status_code == 200
    assert "pmt-doc-content-heading-2" in response.text
    assert '<table class="pmt-doc-table">' in response.text
    assert "| Role | Access |" not in response.text
    assert '<pre class="pmt-context-content"' not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    assert "<script>alert(1)</script>" not in response.text
    assert (
        'href="https://example.test/spec" target="_blank" rel="noopener noreferrer"'
        in response.text
    )
    assert 'href="javascript:alert(1)"' not in response.text
    assert 'aria-current="page"' in response.text
    assert "Access Right Supervisor" in response.text
    assert "Overview / Access Right Supervisor" in response.text
    assert "Detail teknis" in response.text
    assert "pmt-doc-technical-grid" in response.text
    assert (
        f'action="/pmt/tasks/{task["task_key"]}/context/{attached["id"]}/remove"' in response.text
    )
