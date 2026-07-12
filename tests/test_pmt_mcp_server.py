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


def test_pmt_mcp_requires_https_for_remote_api(monkeypatch):
    monkeypatch.setenv("MCP_PMT_API_URL", "http://pmt.example.test")
    monkeypatch.setenv("MCP_PMT_API_TOKEN", "secret")

    try:
        pmt_mcp_server._api_config()
    except ValueError as exc:
        assert "HTTPS" in str(exc)
    else:
        raise AssertionError("insecure non-local API URL was accepted")
