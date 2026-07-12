from __future__ import annotations

from mcp_transfer_node import pmt_mcp_server


def test_pmt_mcp_maps_agent_identity_and_claim_payload(monkeypatch):
    calls = []

    def fake_request(method, path, *, json_body=None, params=None):
        calls.append((method, path, json_body, params))
        return {"task": {"task_key": "PMT-0001", "claimed_by": "openclaw-server-a"}}

    monkeypatch.setenv("MCP_PMT_AGENT_ID", "openclaw-server-a")
    monkeypatch.setattr(pmt_mcp_server, "_request", fake_request)

    result = pmt_mcp_server.pmt_claim_task("PMT-0001", "claim-1", 600)

    assert result["task"]["claimed_by"] == "openclaw-server-a"
    assert calls == [
        (
            "POST",
            "/tasks/PMT-0001/claim",
            {
                "agent_id": "openclaw-server-a",
                "idempotency_key": "claim-1",
                "lease_seconds": 600,
            },
            None,
        )
    ]


def test_pmt_mcp_maps_task_detail_writes(monkeypatch):
    calls = []

    def fake_request(method, path, *, json_body=None, params=None):
        calls.append((method, path, json_body, params))
        return {"task": {"task_key": "PMT-0001"}}

    monkeypatch.setattr(pmt_mcp_server, "_request", fake_request)

    pmt_mcp_server.pmt_update_task(
        "PMT-0001",
        project="HMX",
        module="core_hr",
        target_branch="Human-Resources",
        source_branch="feat/detail",
    )
    pmt_mcp_server.pmt_add_acceptance_criterion("PMT-0001", "Tests pass")
    pmt_mcp_server.pmt_add_evidence("PMT-0001", "test", label="Pre-push", note="Passed")

    assert calls == [
        (
            "PATCH",
            "/tasks/PMT-0001",
            {
                "project": "HMX",
                "module": "core_hr",
                "target_branch": "Human-Resources",
                "source_branch": "feat/detail",
            },
            None,
        ),
        (
            "POST",
            "/tasks/PMT-0001/criteria",
            {"text": "Tests pass"},
            None,
        ),
        (
            "POST",
            "/tasks/PMT-0001/evidence",
            {
                "evidence_type": "test",
                "label": "Pre-push",
                "url": "",
                "note": "Passed",
            },
            None,
        ),
    ]


def test_pmt_mcp_requires_https_for_remote_api(monkeypatch):
    monkeypatch.setenv("MCP_PMT_API_URL", "http://pmt.example.test")
    monkeypatch.setenv("MCP_PMT_API_TOKEN", "secret")

    try:
        pmt_mcp_server._api_config()
    except ValueError as exc:
        assert "HTTPS" in str(exc)
    else:
        raise AssertionError("insecure non-local API URL was accepted")


def test_pmt_mcp_forwards_run_fencing_token(monkeypatch):
    calls = []

    def fake_request(method, path, *, json_body=None, params=None):
        calls.append((method, path, json_body, params))
        return {"task": {"task_key": "PMT-0001"}}

    monkeypatch.setenv("MCP_PMT_AGENT_ID", "openclaw-server-a")
    monkeypatch.setattr(pmt_mcp_server, "_request", fake_request)

    pmt_mcp_server.pmt_task_heartbeat("PMT-0001", "run-123", 600)
    pmt_mcp_server.pmt_start_task("PMT-0001", "run-123", "Starting")

    assert calls == [
        (
            "POST",
            "/tasks/PMT-0001/heartbeat",
            {
                "agent_id": "openclaw-server-a",
                "run_id": "run-123",
                "lease_seconds": 600,
            },
            None,
        ),
        (
            "POST",
            "/tasks/PMT-0001/transition",
            {
                "agent_id": "openclaw-server-a",
                "run_id": "run-123",
                "status": "in_progress",
                "note": "Starting",
                "blocker": "",
            },
            None,
        ),
    ]


def test_pmt_mcp_agent_control_reads_and_heartbeats(monkeypatch):
    calls = []

    def fake_request(method, path, *, json_body=None, params=None):
        calls.append((method, path, json_body, params))
        return {"agents": []}

    monkeypatch.setenv("MCP_PMT_AGENT_ID", "openclaw-server-a")
    monkeypatch.setattr(pmt_mcp_server, "_request", fake_request)

    pmt_mcp_server.pmt_get_agents(240)
    pmt_mcp_server.pmt_agent_heartbeat()

    assert calls == [
        ("GET", "/agents", None, {"offline_after_seconds": 240}),
        ("POST", "/agents/openclaw-server-a/heartbeat", None, None),
    ]
