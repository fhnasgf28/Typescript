from __future__ import annotations


HEADERS = {"Authorization": "Bearer valid-token", "X-PMT-Agent": "server-a"}


def test_pmt_api_requires_agent_auth(client):
    response = client.get("/api/v1/pmt/tasks")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_pmt_api_create_claim_and_transition(client):
    created_response = client.post(
        "/api/v1/pmt/tasks",
        headers=HEADERS,
        json={
            "title": "Fix employee access",
            "module": "core_hr",
            "priority": "high",
            "acceptance_criteria": ["Self Service reads own employee only"],
            "required_checks": ["access-matrix", "prepush-quality"],
        },
    )
    assert created_response.status_code == 201
    task = created_response.json()["data"]["task"]

    registered = client.post(
        "/api/v1/pmt/agents/register",
        headers=HEADERS,
        json={"agent_id": "server-a", "server_name": "dev-a", "capabilities": ["hmx-code"]},
    )
    assert registered.status_code == 200

    claimed = client.post(
        f"/api/v1/pmt/tasks/{task['task_key']}/claim",
        headers=HEADERS,
        json={
            "agent_id": "server-a",
            "idempotency_key": "api-claim-1",
            "lease_seconds": 600,
        },
    )
    assert claimed.status_code == 200
    claimed_task = claimed.json()["data"]["task"]
    assert claimed_task["claimed_by"] == "server-a"

    transitioned = client.post(
        f"/api/v1/pmt/tasks/{task['task_key']}/transition",
        headers=HEADERS,
        json={
            "agent_id": "server-a",
            "run_id": claimed_task["current_run_id"],
            "status": "in_progress",
            "note": "Inspecting ACL",
        },
    )
    assert transitioned.status_code == 200
    assert transitioned.json()["data"]["task"]["status"] == "in_progress"

    events = client.get(f"/api/v1/pmt/tasks/{task['task_key']}/events", headers=HEADERS)
    assert events.status_code == 200
    assert len(events.json()["data"]["events"]) == 3


def test_pmt_api_task_detail_writes(client):
    registered = client.post(
        "/api/v1/pmt/agents/register",
        headers=HEADERS,
        json={"agent_id": "server-a", "server_name": "dev-a"},
    )
    assert registered.status_code == 200
    created = client.post(
        "/api/v1/pmt/tasks",
        headers=HEADERS,
        json={"title": "Detail API task", "acceptance_criteria": ["First criterion"]},
    ).json()["data"]["task"]
    claimed = client.post(
        f"/api/v1/pmt/tasks/{created['task_key']}/claim",
        headers=HEADERS,
        json={
            "agent_id": "server-a",
            "idempotency_key": "detail-api-claim",
            "lease_seconds": 600,
        },
    )
    assert claimed.status_code == 200

    patched = client.patch(
        f"/api/v1/pmt/tasks/{created['task_key']}",
        headers=HEADERS,
        json={
            "module": "core_hr",
            "source_branch": "feat/detail-api",
            "commit_ref": "abc123",
        },
    )
    assert patched.status_code == 200
    task = patched.json()["data"]["task"]
    assert task["module"] == "core_hr"
    assert task["source_branch"] == "feat/detail-api"

    criterion = task["acceptance_criteria"][0]
    toggled = client.post(
        f"/api/v1/pmt/tasks/{task['task_key']}/criteria/{criterion['id']}/toggle",
        headers=HEADERS,
    )
    assert toggled.status_code == 200
    assert toggled.json()["data"]["task"]["acceptance_criteria"][0]["done"] is True

    evidence = client.post(
        f"/api/v1/pmt/tasks/{task['task_key']}/evidence",
        headers=HEADERS,
        json={
            "evidence_type": "test",
            "label": "Unit tests",
            "url": "https://example.test/test/1",
            "note": "46 passed",
        },
    )
    assert evidence.status_code == 201
    listed = client.get(f"/api/v1/pmt/tasks/{task['task_key']}/evidence", headers=HEADERS)
    assert listed.status_code == 200
    assert listed.json()["data"]["evidence"][0]["label"] == "Unit tests"


def test_pmt_api_rejects_agent_impersonation(client):
    response = client.post(
        "/api/v1/pmt/agents/register",
        headers=HEADERS,
        json={"agent_id": "server-b", "server_name": "dev-b"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_pmt_api_lists_agents_and_accepts_idle_heartbeat(client):
    registered = client.post(
        "/api/v1/pmt/agents/register",
        headers=HEADERS,
        json={
            "agent_id": "server-a",
            "server_name": "dev-a",
            "capabilities": ["hmx-code", "pipeline"],
        },
    )
    assert registered.status_code == 200

    heartbeat = client.post("/api/v1/pmt/agents/server-a/heartbeat", headers=HEADERS)
    listed = client.get("/api/v1/pmt/agents", headers=HEADERS)

    assert heartbeat.status_code == 200
    assert listed.status_code == 200
    agent = listed.json()["data"]["agents"][0]
    assert agent["agent_id"] == "server-a"
    assert agent["effective_status"] == "online"
    assert agent["capabilities"] == ["hmx-code", "pipeline"]


def test_pmt_api_rejects_detail_write_without_active_ownership(client):
    created = client.post(
        "/api/v1/pmt/tasks", headers=HEADERS, json={"title": "Unclaimed task"}
    ).json()["data"]["task"]

    response = client.patch(
        f"/api/v1/pmt/tasks/{created['task_key']}",
        headers=HEADERS,
        json={"module": "core_hr"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CLAIM_CONFLICT"


def test_pmt_dashboard_requires_login_and_can_create_task(client):
    assert client.get("/pmt", follow_redirects=False).status_code == 303
    login = client.post("/login", data={"password": "admin-password"})
    assert login.status_code == 200

    created = client.post(
        "/pmt/tasks",
        data={"title": "Dashboard task", "module": "core_hr", "priority": "urgent"},
        follow_redirects=False,
    )
    assert created.status_code == 303

    dashboard = client.get("/pmt")
    assert dashboard.status_code == 200
    assert "Dashboard task" in dashboard.text
    assert "PMT-0001" in dashboard.text
    assert "bootstrap@5.3.3" in dashboard.text
    assert 'id="newTaskModal"' in dashboard.text
    assert 'class="modal-content pmt-modal-form' in dashboard.text
    assert "/static/pmt.css?v=20260712-sprint2a" in dashboard.text
    assert 'id="task-search"' in dashboard.text
    assert "/pmt/tasks/PMT-0001" in dashboard.text
    css = client.get("/static/pmt.css?v=20260712-sprint2a")
    assert "max-height: calc(100dvh - 1.5rem)" in css.text
    assert "overflow-y: auto" in css.text


def test_pmt_agent_and_sync_centers_require_login_and_render(client):
    assert client.get("/pmt/agents", follow_redirects=False).status_code == 303
    assert client.get("/pmt/sync", follow_redirects=False).status_code == 303
    client.post("/login", data={"password": "admin-password"})
    client.post(
        "/api/v1/pmt/agents/register",
        headers=HEADERS,
        json={"agent_id": "server-a", "server_name": "dev-a", "capabilities": ["hmx"]},
    )

    agents = client.get("/pmt/agents")
    sync = client.get("/pmt/sync")

    assert agents.status_code == 200
    assert "Agent Control Center" in agents.text
    assert "server-a" in agents.text
    assert sync.status_code == 200
    assert "Sheet Sync Center" in sync.text
    assert "PMT tidak menulis kembali" in sync.text


def test_pmt_web_can_create_and_pause_read_only_sync(client):
    client.post("/login", data={"password": "admin-password"})
    created = client.post(
        "/pmt/sync/schedules",
        data={
            "name": "Farhan To-Do",
            "csv_url": "https://docs.google.com/spreadsheets/d/example/export?format=csv&gid=0",
            "interval_minutes": "15",
            "assignee": "Farhan",
            "dev_status": "To-Do",
            "project": "HMX",
            "target_branch": "Human-Resources",
        },
        follow_redirects=True,
    )
    assert created.status_code == 200
    assert "Farhan To-Do" in created.text
    assert "Enabled" in created.text


def test_pmt_task_detail_web_workflow(client, settings):
    client.post("/login", data={"password": settings.web_admin_password})
    client.post(
        "/pmt/tasks",
        data={"title": "Detailed task", "module": "core_hr", "priority": "normal"},
    )

    detail = client.get("/pmt/tasks/PMT-0001")
    assert detail.status_code == 200
    assert "Acceptance Criteria" in detail.text
    assert "Activity Timeline" in detail.text
    assert 'id="editTaskModal"' in detail.text

    edited = client.post(
        "/pmt/tasks/PMT-0001/edit",
        data={
            "title": "Detailed task updated",
            "description": "Full requirement",
            "project": "HMX",
            "module": "core_hr",
            "menu": "Employee",
            "assignee": "Farhan",
            "priority": "high",
            "target_branch": "Human-Resources",
            "source_branch": "feat/detail",
            "commit_ref": "abc123",
            "mr_url": "https://example.test/mr/1",
            "pipeline_url": "https://example.test/pipeline/1",
            "version": "1",
        },
        follow_redirects=True,
    )
    assert edited.status_code == 200
    assert "Detailed task updated" in edited.text
    assert "feat/detail" in edited.text

    criterion = client.post(
        "/pmt/tasks/PMT-0001/criteria",
        data={"text": "All access tests pass"},
        follow_redirects=True,
    )
    assert criterion.status_code == 200
    assert "All access tests pass" in criterion.text

    evidence = client.post(
        "/pmt/tasks/PMT-0001/evidence",
        data={
            "evidence_type": "test",
            "label": "Pre-push passed",
            "url": "https://example.test/evidence/1",
            "note": "All hooks passed",
        },
        follow_redirects=True,
    )
    assert evidence.status_code == 200
    assert "Pre-push passed" in evidence.text

    transitioned = client.post(
        "/pmt/tasks/PMT-0001/status",
        data={"task_status": "inbox", "note": "Needs triage"},
        follow_redirects=True,
    )
    assert transitioned.status_code == 200
    assert ">Inbox<" in transitioned.text
