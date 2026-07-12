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
    assert claimed.json()["data"]["task"]["claimed_by"] == "server-a"

    transitioned = client.post(
        f"/api/v1/pmt/tasks/{task['task_key']}/transition",
        headers=HEADERS,
        json={"agent_id": "server-a", "status": "in_progress", "note": "Inspecting ACL"},
    )
    assert transitioned.status_code == 200
    assert transitioned.json()["data"]["task"]["status"] == "in_progress"

    events = client.get(f"/api/v1/pmt/tasks/{task['task_key']}/events", headers=HEADERS)
    assert events.status_code == 200
    assert len(events.json()["data"]["events"]) == 3


def test_pmt_api_rejects_agent_impersonation(client):
    response = client.post(
        "/api/v1/pmt/agents/register",
        headers=HEADERS,
        json={"agent_id": "server-b", "server_name": "dev-b"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


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
