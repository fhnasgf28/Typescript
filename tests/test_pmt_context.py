from __future__ import annotations

import os
from pathlib import Path

import pytest

from mcp_transfer_node.config import TransferSettings
from mcp_transfer_node.pmt_context import GoogleDocsContextService
from mcp_transfer_node.pmt_gdocs import GoogleDocsError, parse_google_doc_payload
from mcp_transfer_node.pmt_store import PmtStore, TaskInput


def _payload(text: str = "hello", *, revision: str = "r1", title: str = "Spec"):
    return {
        "documentId": "doc123",
        "title": title,
        "revisionId": revision,
        "tabs": [
            {
                "tabProperties": {"tabId": "t.0", "title": "Main"},
                "documentTab": {
                    "body": {
                        "content": [
                            {
                                "paragraph": {
                                    "elements": [{"textRun": {"content": text + "\n"}}],
                                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                                }
                            }
                        ]
                    }
                },
                "childTabs": [],
            }
        ],
    }


def _snapshot(text: str = "hello", *, revision: str = "r1", title: str = "Spec"):
    return parse_google_doc_payload(_payload(text, revision=revision, title=title))


def _store(tmp_path: Path):
    store = PmtStore(tmp_path / "pmt.sqlite3")
    store.initialize()
    task = store.create_task(TaskInput(title="Context task"), actor="Farhan")
    return store, task


def _settings(tmp_path: Path, credential: Path | None) -> TransferSettings:
    return TransferSettings(
        server_name="test",
        base_dir=tmp_path / "runtime",
        max_file_mb=50,
        public_url="https://test.example",
        web_admin_password="password",
        session_secret="a" * 32,
        google_docs_service_account_file=credential,
    )


def test_context_schema_lifecycle_idempotency_and_no_task_version_bump(tmp_path: Path):
    store, task = _store(tmp_path)
    url = "https://docs.google.com/document/d/doc123/edit?tab=t.0"
    initial_version = task["version"]
    attached = store.save_task_context_snapshot(
        task["task_key"],
        source_url=url,
        snapshot=_snapshot(),
        actor="Farhan",
        operation="attach",
        expected_version=initial_version,
    )
    assert attached["context_version"] == 1
    assert attached["changed"] is True
    assert store.get_task(task["task_key"])["version"] == initial_version
    document = store.get_task_context_document(task["task_key"], attached["id"])
    assert document["tabs"][0]["text"] == "hello"

    unchanged = store.save_task_context_snapshot(
        task["task_key"],
        source_url=url,
        snapshot=_snapshot(revision="r2"),
        actor="Farhan",
        operation="refresh",
        context_ref=attached["id"],
        expected_version=initial_version,
        expected_context_version=1,
    )
    assert unchanged["changed"] is False
    assert unchanged["context_version"] == 1
    assert unchanged["revision_id"] == "r1"

    changed = store.save_task_context_snapshot(
        task["task_key"],
        source_url=url,
        snapshot=_snapshot("changed"),
        actor="Farhan",
        operation="refresh",
        context_ref=attached["id"],
        expected_version=initial_version,
        expected_context_version=1,
    )
    assert changed["context_version"] == 2
    assert store.get_task(task["task_key"])["version"] == initial_version

    stale_but_same = store.save_task_context_snapshot(
        task["task_key"],
        source_url=url,
        snapshot=_snapshot("changed", revision="r3"),
        actor="Farhan",
        operation="refresh",
        context_ref=attached["id"],
        expected_version=initial_version,
        expected_context_version=1,
    )
    assert stale_but_same["changed"] is False
    assert stale_but_same["context_version"] == 2

    with pytest.raises(PermissionError, match="context changed"):
        store.save_task_context_snapshot(
            task["task_key"],
            source_url=url,
            snapshot=_snapshot("third"),
            actor="Farhan",
            operation="refresh",
            context_ref=attached["id"],
            expected_version=initial_version,
            expected_context_version=1,
        )

    removed = store.remove_task_context_document(
        task["task_key"],
        attached["id"],
        actor="Farhan",
        expected_version=initial_version,
        expected_context_version=2,
    )
    assert removed["id"] == attached["id"]
    assert store.list_task_context_documents(task["task_key"]) == []
    event_payloads = [item["payload"] for item in store.task_events(task["task_key"])]
    assert all(
        "hello" not in str(payload) and "changed" not in str(payload) for payload in event_payloads
    )


@pytest.mark.asyncio
async def test_service_checks_owner_run_and_task_version_before_and_after_fetch(tmp_path: Path):
    store, task = _store(tmp_path)
    store.register_agent("agent-a", "server", [])
    claimed = store.claim_task(task["task_key"], "agent-a", "claim-context", 600)
    credential = tmp_path / "service-account.json"
    credential.write_text("{}", encoding="utf-8")
    os.chmod(credential, 0o600)
    calls = 0

    async def fetcher(_url, _credential, **_kwargs):
        nonlocal calls
        calls += 1
        return _snapshot()

    service = GoogleDocsContextService(store, _settings(tmp_path, credential), fetcher=fetcher)
    with pytest.raises(PermissionError, match="active task owner"):
        await service.attach(
            task["task_key"],
            "https://docs.google.com/document/d/doc123/edit?tab=t.0",
            actor="other",
            expected_version=claimed["version"],
            expected_owner="other",
            expected_run_id=claimed["current_run_id"],
        )
    assert calls == 0

    async def racing_fetcher(_url, _credential, **_kwargs):
        current = store.get_task(task["task_key"])
        store.update_task(
            task["task_key"],
            actor="admin",
            title=current["title"] + " updated",
            description=current["description"],
            project=current["project"],
            module=current["module"],
            menu=current["menu"],
            assignee=current["assignee"],
            priority=current["priority"],
            required_checks=current["required_checks"],
            target_branch=current["target_branch"],
            expected_version=current["version"],
        )
        return _snapshot()

    service = GoogleDocsContextService(
        store, _settings(tmp_path, credential), fetcher=racing_fetcher
    )
    with pytest.raises(PermissionError, match="task changed"):
        await service.attach(
            task["task_key"],
            "https://docs.google.com/document/d/doc123/edit?tab=t.0",
            actor="agent-a",
            expected_version=claimed["version"],
            expected_owner="agent-a",
            expected_run_id=claimed["current_run_id"],
        )
    assert store.list_task_context_documents(task["task_key"]) == []

    current = store.get_task(task["task_key"])

    async def fence_racing_fetcher(_url, _credential, **_kwargs):
        store.transition_task(
            task["task_key"],
            "agent-a",
            current["current_run_id"],
            "todo",
            note="released during fetch",
        )
        return _snapshot()

    service = GoogleDocsContextService(
        store, _settings(tmp_path, credential), fetcher=fence_racing_fetcher
    )
    with pytest.raises(PermissionError, match="active task owner"):
        await service.attach(
            task["task_key"],
            "https://docs.google.com/document/d/doc123/edit?tab=t.0",
            actor="agent-a",
            expected_version=current["version"],
            expected_owner="agent-a",
            expected_run_id=current["current_run_id"],
        )
    assert store.list_task_context_documents(task["task_key"]) == []


@pytest.mark.asyncio
async def test_service_fails_closed_without_owner_only_credentials(tmp_path: Path):
    store, task = _store(tmp_path)
    called = False

    async def fetcher(*_args, **_kwargs):
        nonlocal called
        called = True
        return _snapshot()

    service = GoogleDocsContextService(store, _settings(tmp_path, None), fetcher=fetcher)
    with pytest.raises(GoogleDocsError, match="not configured"):
        await service.attach(
            task["task_key"],
            "https://docs.google.com/document/d/doc123",
            actor="Farhan",
            expected_version=task["version"],
        )
    assert called is False


@pytest.mark.asyncio
@pytest.mark.parametrize("unsafe_kind", ["group_readable", "wrong_owner"])
async def test_service_rejects_unsafe_credential_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unsafe_kind: str
):
    store, task = _store(tmp_path)
    credential = tmp_path / "service-account.json"
    credential.write_text("{}", encoding="utf-8")
    os.chmod(credential, 0o600)
    if unsafe_kind == "group_readable":
        os.chmod(credential, 0o640)
    else:
        current_uid = os.geteuid()
        monkeypatch.setattr("mcp_transfer_node.pmt_context.os.geteuid", lambda: current_uid + 1)

    called = False

    async def fetcher(*_args, **_kwargs):
        nonlocal called
        called = True
        return _snapshot()

    service = GoogleDocsContextService(store, _settings(tmp_path, credential), fetcher=fetcher)
    with pytest.raises(GoogleDocsError, match="owner-owned, owner-only"):
        await service.attach(
            task["task_key"],
            "https://docs.google.com/document/d/doc123",
            actor="Farhan",
            expected_version=task["version"],
        )
    assert called is False
