from __future__ import annotations

import asyncio
import json
import re
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from mcp_transfer_node.app import create_app
from mcp_transfer_node.pmt_drive import (
    CALLBACK_PATH,
    DRIVE_METADATA_SCOPE,
    SHEETS_READONLY_SCOPE,
    DriveWatchError,
    _debounced_runner,
    derive_channel_token,
    register_drive_watch,
    retry_drive_channel_cleanups,
    run_due_drive_events,
    stop_active_drive_watches,
    token_hash,
)
from mcp_transfer_node.pmt_store import MAX_DRIVE_EVENT_ATTEMPTS, PmtStore
from mcp_transfer_node.pmt_sheet import SheetSyncBusy, sync_google_sheet

FILE_ID = "sheet_Abc-123"
CSV_URL = f"https://docs.google.com/spreadsheets/d/{FILE_ID}/export?format=csv&gid=0"
RESOURCE_ID = "resource_abc-123"
RESOURCE_URI = f"https://www.googleapis.com/drive/v3/files/{FILE_ID}"


def drive_settings(settings):
    return replace(
        settings,
        google_docs_service_account_file=settings.base_dir / "owner-only.json",
        pmt_drive_watch_enabled=True,
        pmt_drive_spreadsheet_id=FILE_ID,
        pmt_drive_csv_url=CSV_URL,
        pmt_drive_webhook_secret="w" * 32,
        pmt_drive_webhook_callback_url=(
            f"{settings.public_url}/api/v1/pmt/drive-notifications/bug-tracker"
        ),
    )


async def fake_token(_path, scopes):
    assert scopes == (DRIVE_METADATA_SCOPE,)
    return "oauth-access-token"


async def fake_sheet_token(_settings, _provider, scopes):
    assert scopes == (SHEETS_READONLY_SCOPE,)
    return "sheet-access-token"


@pytest.mark.asyncio
async def test_watch_registration_contract_is_pinned_bounded_and_secret_safe(settings):
    configured = drive_settings(settings)
    store = PmtStore(configured.pmt_db_path)
    store.initialize()
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["authorization"]
        captured["payload"] = json.loads(request.content)
        payload = captured["payload"]
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={
                "id": payload["id"],
                "resourceId": RESOURCE_ID,
                "resourceUri": RESOURCE_URI,
                "expiration": payload["expiration"],
            },
            request=request,
        )

    result = await register_drive_watch(
        store,
        configured,
        transport=httpx.MockTransport(handler),
        access_token_provider=fake_token,
    )

    assert captured["url"] == (
        f"https://www.googleapis.com/drive/v3/files/{FILE_ID}/watch?supportsAllDrives=true"
    )
    assert captured["authorization"] == "Bearer oauth-access-token"
    assert captured["payload"]["type"] == "web_hook"
    assert captured["payload"]["address"].endswith(CALLBACK_PATH)
    assert len(captured["payload"]["id"]) == 36
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", captured["payload"]["token"])
    row = store.get_drive_channel(result["channel_id"])
    assert row["token_hash"] == token_hash(captured["payload"]["token"])
    assert captured["payload"]["token"] not in json.dumps(result)


@pytest.mark.asyncio
async def test_watch_response_redirect_size_and_provider_error_are_sanitized(settings):
    configured = drive_settings(settings)

    @pytest.mark.parametrize("unused", [1])
    def _placeholder(unused):
        return unused

    for response in (
        lambda request: httpx.Response(
            302, headers={"location": "https://evil.test"}, request=request
        ),
        lambda request: httpx.Response(
            500,
            headers={"content-type": "application/json"},
            content=b'{"error":"PRIVATE_PROVIDER_DETAIL"}',
            request=request,
        ),
        lambda request: httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b"x" * (64 * 1024 + 1),
            request=request,
        ),
    ):
        store = PmtStore(configured.pmt_db_path.with_name(f"{id(response)}.sqlite3"))
        store.initialize()
        with pytest.raises(DriveWatchError) as caught:
            await register_drive_watch(
                store,
                configured,
                transport=httpx.MockTransport(response),
                access_token_provider=fake_token,
            )
        assert "PRIVATE_PROVIDER_DETAIL" not in str(caught.value)


@pytest.mark.asyncio
async def test_invalid_watch_response_queues_orphan_cleanup_when_resource_is_usable(settings):
    configured = drive_settings(settings)
    store = PmtStore(configured.pmt_db_path)
    store.initialize()

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if str(request.url).endswith("/channels/stop"):
            return httpx.Response(500, request=request)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={
                "id": "different-channel",
                "resourceId": RESOURCE_ID,
                "resourceUri": RESOURCE_URI,
                "expiration": payload["expiration"],
            },
            request=request,
        )

    with pytest.raises(DriveWatchError, match="different channel ID"):
        await register_drive_watch(
            store,
            configured,
            transport=httpx.MockTransport(handler),
            access_token_provider=fake_token,
        )
    status = store.drive_watch_status(FILE_ID)
    assert status["channels"][0]["cleanup_status"] == "pending"
    assert status["channels"][0]["bound"] == 1


def _channel(store: PmtStore, configured, *, bound: bool = True):
    channel_id = "12345678-1234-4123-8123-123456789abc"
    token = derive_channel_token(configured.pmt_drive_webhook_secret, channel_id, FILE_ID)
    store.create_pending_drive_channel(
        channel_id, FILE_ID, token_hash(token), datetime.now(timezone.utc) + timedelta(hours=1)
    )
    if bound:
        store.bind_drive_channel(
            channel_id,
            RESOURCE_ID,
            RESOURCE_URI,
            datetime.now(timezone.utc) + timedelta(hours=1),
        )
    return channel_id, token


def _headers(channel_id: str, token: str, number: int, state: str = "update"):
    headers = {
        "X-Goog-Channel-ID": channel_id,
        "X-Goog-Channel-Token": token,
        "X-Goog-Resource-ID": RESOURCE_ID,
        "X-Goog-Resource-State": state,
        "X-Goog-Message-Number": str(number),
        "X-Goog-Resource-URI": RESOURCE_URI,
    }
    if state == "update":
        headers["X-Goog-Changed"] = "content,properties"
    return headers


def test_prebind_sync_race_is_durable_but_does_not_enqueue(settings):
    configured = drive_settings(settings)
    store = PmtStore(configured.pmt_db_path)
    store.initialize()
    channel_id, token = _channel(store, configured, bound=False)

    outcome = store.record_drive_notification(channel_id, token_hash(token), RESOURCE_ID, 1, "sync")
    assert outcome == "ignored"
    store.bind_drive_channel(
        channel_id,
        RESOURCE_ID,
        RESOURCE_URI,
        datetime.now(timezone.utc) + timedelta(hours=1),
    )
    assert store.claim_drive_events("worker") is None


def test_stopped_expired_unknown_channels_and_registration_lease_are_rejected(settings):
    configured = drive_settings(settings)
    store = PmtStore(configured.pmt_db_path)
    store.initialize()
    channel_id, token = _channel(store, configured)
    store.stop_drive_channel(channel_id)
    with pytest.raises(PermissionError):
        store.record_drive_notification(channel_id, token_hash(token), RESOURCE_ID, 1, "sync")
    with pytest.raises(PermissionError):
        store.record_drive_notification("unknown", token_hash(token), RESOURCE_ID, 1, "sync")

    expired_id = "22345678-1234-4123-8123-123456789abc"
    expired_token = derive_channel_token(configured.pmt_drive_webhook_secret, expired_id, FILE_ID)
    store.create_pending_drive_channel(
        expired_id,
        FILE_ID,
        token_hash(expired_token),
        datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    with pytest.raises(PermissionError):
        store.record_drive_notification(
            expired_id, token_hash(expired_token), RESOURCE_ID, 1, "sync"
        )
    assert store.claim_drive_watch_lease(FILE_ID, "worker-a") is True
    assert store.claim_drive_watch_lease(FILE_ID, "worker-b") is False
    store.release_drive_watch_lease(FILE_ID, "worker-a")
    assert store.claim_drive_watch_lease(FILE_ID, "worker-b") is True
    assert store.drive_renewal_retry_due(FILE_ID) is True
    store.record_drive_renewal_result(FILE_ID, success=False, error_type="DriveWatchError")
    assert store.drive_renewal_retry_due(FILE_ID) is False
    store.record_drive_renewal_result(FILE_ID, success=True)
    assert store.drive_renewal_retry_due(FILE_ID) is True


def test_webhook_validates_body_auth_resource_state_order_and_dedupe(settings, monkeypatch):
    configured = drive_settings(settings)
    store = PmtStore(configured.pmt_db_path)
    store.initialize()
    channel_id, token = _channel(store, configured)
    monkeypatch.setattr("mcp_transfer_node.pmt_drive.launch_debounced_runner", lambda *_: None)

    with TestClient(create_app(configured), base_url="https://testserver") as client:
        assert client.post(CALLBACK_PATH, headers=_headers(channel_id, token, 2)).status_code == 202
        assert client.post(CALLBACK_PATH, headers=_headers(channel_id, token, 2)).status_code == 204
        assert client.post(CALLBACK_PATH, headers=_headers(channel_id, token, 1)).status_code == 403
        assert (
            client.post(CALLBACK_PATH, headers=_headers(channel_id, "A" * 43, 3)).status_code == 403
        )
        forged_resource = _headers(channel_id, token, 3)
        forged_resource["X-Goog-Resource-ID"] = "other"
        assert client.post(CALLBACK_PATH, headers=forged_resource).status_code == 403
        permissions_only = _headers(channel_id, token, 3)
        permissions_only["X-Goog-Changed"] = "permissions"
        assert client.post(CALLBACK_PATH, headers=permissions_only).status_code == 202
        without_changed = _headers(channel_id, token, 4)
        without_changed.pop("X-Goog-Changed")
        assert client.post(CALLBACK_PATH, headers=without_changed).status_code == 202
        assert (
            client.post(
                CALLBACK_PATH, headers=_headers(channel_id, token, 4), content=b"x"
            ).status_code
            == 400
        )


def test_webhook_sync_acknowledges_without_launching_fetch(settings, monkeypatch):
    configured = drive_settings(settings)
    store = PmtStore(configured.pmt_db_path)
    store.initialize()
    channel_id, token = _channel(store, configured)
    launched = []
    monkeypatch.setattr(
        "mcp_transfer_node.pmt_drive.launch_debounced_runner", lambda *_: launched.append(True)
    )
    with TestClient(create_app(configured), base_url="https://testserver") as client:
        response = client.post(CALLBACK_PATH, headers=_headers(channel_id, token, 1, "sync"))
    assert response.status_code == 204
    assert launched == []


@pytest.mark.asyncio
async def test_debounce_coalesces_and_fences_concurrent_sync(settings, monkeypatch):
    configured = drive_settings(settings)
    store = PmtStore(configured.pmt_db_path)
    store.initialize()
    channel_id, token = _channel(store, configured)
    store.record_drive_notification(channel_id, token_hash(token), RESOURCE_ID, 2, "update")
    store.record_drive_notification(channel_id, token_hash(token), RESOURCE_ID, 3, "update")
    with store._transaction() as db:
        db.execute(
            "UPDATE drive_notification_events SET available_at=?,next_attempt_at=?",
            ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),) * 2,
        )
    calls = 0

    async def fake_sync(_store, payload, *, actor, bearer_token):
        nonlocal calls
        assert bearer_token == "sheet-access-token"
        calls += 1
        await asyncio.sleep(0.01)
        return {"matched": 1, "imported": ["PMT-0001"], "already_present": [], "source_id": "x"}

    monkeypatch.setattr("mcp_transfer_node.pmt_drive.sync_google_sheet", fake_sync)
    monkeypatch.setattr("mcp_transfer_node.pmt_drive._access_token", fake_sheet_token)
    first, second = await asyncio.gather(
        run_due_drive_events(store, configured, "worker-a"),
        run_due_drive_events(store, configured, "worker-b"),
    )
    assert calls == 1
    assert {first["status"], second["status"]} == {"succeeded", "idle"}
    assert first.get("events", second.get("events")) == 2


@pytest.mark.asyncio
async def test_failed_event_retries_after_restart_recovery(settings, monkeypatch):
    configured = drive_settings(settings)
    store = PmtStore(configured.pmt_db_path)
    store.initialize()
    channel_id, token = _channel(store, configured)
    store.record_drive_notification(channel_id, token_hash(token), RESOURCE_ID, 2, "update")
    with store._transaction() as db:
        due = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        db.execute(
            "UPDATE drive_notification_events SET available_at=?,next_attempt_at=?", (due, due)
        )

    async def failing(*_args, **_kwargs):
        raise RuntimeError("safe failure")

    monkeypatch.setattr("mcp_transfer_node.pmt_drive.sync_google_sheet", failing)
    monkeypatch.setattr("mcp_transfer_node.pmt_drive._access_token", fake_sheet_token)
    failed = await run_due_drive_events(store, configured, "worker-a")
    assert failed["status"] == "failed"
    assert "safe failure" not in json.dumps(failed)
    assert await run_due_drive_events(store, configured, "worker-b") == {"status": "idle"}
    with store._transaction() as db:
        db.execute(
            "UPDATE drive_notification_events SET next_attempt_at=?",
            ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),),
        )

    async def success(*_args, **_kwargs):
        return {"matched": 0, "imported": [], "already_present": [], "source_id": "x"}

    monkeypatch.setattr("mcp_transfer_node.pmt_drive.sync_google_sheet", success)
    restarted_store = PmtStore(configured.pmt_db_path)
    restarted_store.initialize()
    assert (await run_due_drive_events(restarted_store, configured, "worker-b"))[
        "status"
    ] == "succeeded"


@pytest.mark.asyncio
async def test_failed_event_retries_are_bounded(settings, monkeypatch):
    configured = drive_settings(settings)
    store = PmtStore(configured.pmt_db_path)
    store.initialize()
    channel_id, token = _channel(store, configured)
    store.record_drive_notification(channel_id, token_hash(token), RESOURCE_ID, 2, "update")
    due = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with store._transaction() as db:
        db.execute(
            """UPDATE drive_notification_events SET available_at=?,next_attempt_at=?,
                attempts=?""",
            (due, due, MAX_DRIVE_EVENT_ATTEMPTS - 1),
        )

    async def failing(*_args, **_kwargs):
        raise RuntimeError("PRIVATE_PROVIDER_DETAIL")

    monkeypatch.setattr("mcp_transfer_node.pmt_drive.sync_google_sheet", failing)
    monkeypatch.setattr("mcp_transfer_node.pmt_drive._access_token", fake_sheet_token)
    result = await run_due_drive_events(store, configured, "worker-a")
    assert result["status"] == "failed"
    assert "PRIVATE_PROVIDER_DETAIL" not in json.dumps(result)
    with store._connect() as db:
        event = db.execute(
            "SELECT status,attempts,result FROM drive_notification_events"
        ).fetchone()
    assert event["status"] == "exhausted"
    assert event["attempts"] == MAX_DRIVE_EVENT_ATTEMPTS
    assert "PRIVATE_PROVIDER_DETAIL" not in event["result"]
    assert await run_due_drive_events(store, configured, "worker-b") == {"status": "idle"}


@pytest.mark.asyncio
async def test_renewal_overlaps_then_stops_old_channel(settings):
    configured = drive_settings(settings)
    store = PmtStore(configured.pmt_db_path)
    store.initialize()
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((str(request.url), json.loads(request.content)))
        payload = calls[-1][1]
        if str(request.url).endswith("/channels/stop"):
            return httpx.Response(204, request=request)
        suffix = len([url for url, _ in calls if "/files/" in url])
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={
                "id": payload["id"],
                "resourceId": f"resource-{suffix}",
                "resourceUri": RESOURCE_URI,
                "expiration": payload["expiration"],
            },
            request=request,
        )

    transport = httpx.MockTransport(handler)
    first = await register_drive_watch(
        store, configured, transport=transport, access_token_provider=fake_token
    )
    second = await register_drive_watch(
        store, configured, transport=transport, access_token_provider=fake_token
    )
    assert first["channel_id"] != second["channel_id"]
    assert second["replaced"] == 1
    assert second["stopped"] == 1
    status = store.drive_watch_status(FILE_ID)
    assert [row["state"] for row in status["channels"]].count("active") == 1
    stop_payload = next(payload for url, payload in calls if url.endswith("/channels/stop"))
    assert set(stop_payload) == {"id", "resourceId"}


def test_admin_drive_controls_require_login_and_csrf(settings, monkeypatch):
    configured = drive_settings(settings)

    async def fake_register(*_args, **_kwargs):
        return {"expiration_at": "future", "replaced": 0}

    monkeypatch.setattr("mcp_transfer_node.pmt_web.register_drive_watch", fake_register)
    with TestClient(create_app(configured), base_url="https://testserver") as client:
        assert client.get("/pmt/sync/drive-watch/status", follow_redirects=False).status_code == 303
        login = client.post(
            "/login", data={"username": "admin", "password": configured.web_admin_password}
        )
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', login.text).group(1)
        assert (
            client.post("/pmt/sync/drive-watch/register", data={"csrf_token": "wrong"}).status_code
            == 403
        )
        accepted = client.post(
            "/pmt/sync/drive-watch/register",
            data={"csrf_token": csrf},
            follow_redirects=False,
        )
        assert accepted.status_code == 303
        page = client.get("/pmt/sync")
        assert "Google Drive watch" in page.text
        assert configured.pmt_drive_webhook_secret not in page.text
        assert "resource_" not in page.text


@pytest.mark.asyncio
async def test_stop_persists_desired_state_and_worker_does_not_reregister(settings, monkeypatch):
    configured = drive_settings(settings)
    store = PmtStore(configured.pmt_db_path)
    store.initialize()
    _channel(store, configured)

    async def stopped(*_args, **_kwargs):
        raise DriveWatchError("remote unavailable")

    monkeypatch.setattr("mcp_transfer_node.pmt_drive.stop_remote_channel", stopped)
    result = await stop_active_drive_watches(store, configured)
    assert result["marked_stopped"] == 1
    assert store.drive_watch_desired(FILE_ID) is False
    assert (
        store.get_drive_channel("12345678-1234-4123-8123-123456789abc")["cleanup_status"]
        == "failed"
    )

    monkeypatch.setattr("mcp_transfer_node.pmt_worker.load_settings", lambda: configured)
    registrations = []

    async def should_not_register(*_args, **_kwargs):
        registrations.append(True)

    monkeypatch.setattr("mcp_transfer_node.pmt_worker.register_drive_watch", should_not_register)
    monkeypatch.setattr(
        "mcp_transfer_node.pmt_worker.retry_drive_channel_cleanups",
        lambda *_args, **_kwargs: asyncio.sleep(0, result={"attempted": 0, "succeeded": 0}),
    )
    from mcp_transfer_node import pmt_worker

    await pmt_worker.run_once("worker-after-stop")
    assert registrations == []


@pytest.mark.asyncio
async def test_enabled_initial_database_requires_explicit_register(settings, monkeypatch):
    configured = drive_settings(settings)
    store = PmtStore(configured.pmt_db_path)
    store.initialize()
    assert store.drive_watch_desired(FILE_ID) is False
    monkeypatch.setattr("mcp_transfer_node.pmt_worker.load_settings", lambda: configured)
    monkeypatch.setattr(
        "mcp_transfer_node.pmt_worker.retry_drive_channel_cleanups",
        lambda *_args, **_kwargs: asyncio.sleep(0, result={"attempted": 0, "succeeded": 0}),
    )
    registrations = []
    monkeypatch.setattr(
        "mcp_transfer_node.pmt_worker.register_drive_watch",
        lambda *_args, **_kwargs: registrations.append(True),
    )
    from mcp_transfer_node import pmt_worker

    await pmt_worker.run_once("initial-worker")
    assert registrations == []


@pytest.mark.asyncio
async def test_cleanup_failure_is_retried_durably(settings):
    configured = drive_settings(settings)
    store = PmtStore(configured.pmt_db_path)
    store.initialize()
    channel_id, _token = _channel(store, configured)
    store.stop_drive_channel(channel_id)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(204 if calls > 1 else 500, request=request)

    first = await retry_drive_channel_cleanups(
        store,
        configured,
        transport=httpx.MockTransport(handler),
        access_token_provider=fake_token,
    )
    assert first == {"attempted": 1, "succeeded": 0}
    with store._transaction() as db:
        db.execute(
            "UPDATE drive_watch_channels SET cleanup_next_attempt_at=? WHERE channel_id=?",
            ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), channel_id),
        )
    second = await retry_drive_channel_cleanups(
        store,
        configured,
        transport=httpx.MockTransport(handler),
        access_token_provider=fake_token,
    )
    assert second == {"attempted": 1, "succeeded": 1}
    assert store.get_drive_channel(channel_id)["cleanup_status"] == "succeeded"


@pytest.mark.asyncio
async def test_debounce_runner_follows_event_arriving_while_active(settings, monkeypatch):
    configured = drive_settings(settings)
    store = PmtStore(configured.pmt_db_path)
    store.initialize()
    channel_id, token = _channel(store, configured)
    store.record_drive_notification(channel_id, token_hash(token), RESOURCE_ID, 2, "update")
    with store._transaction() as db:
        due = (datetime.now(timezone.utc) + timedelta(seconds=0.05)).isoformat()
        db.execute(
            "UPDATE drive_notification_events SET available_at=?,next_attempt_at=?", (due, due)
        )
    runs = []

    async def local_run(local_store, _settings):
        claim = local_store.claim_drive_events("debounce-test")
        if claim:
            runs.append(claim["event_ids"])
            local_store.finish_drive_events(
                claim["run_id"], "debounce-test", success=True, result={"matched": 0}
            )

    monkeypatch.setattr("mcp_transfer_node.pmt_drive.run_due_drive_events", local_run)
    runner = asyncio.create_task(_debounced_runner(store, configured))
    await asyncio.sleep(0.04)
    store.record_drive_notification(channel_id, token_hash(token), RESOURCE_ID, 3, "update")
    with store._transaction() as db:
        due = (datetime.now(timezone.utc) + timedelta(seconds=0.05)).isoformat()
        db.execute(
            """UPDATE drive_notification_events SET available_at=?,next_attempt_at=?
                WHERE message_number=3""",
            (due, due),
        )
    await asyncio.wait_for(runner, timeout=1)
    assert len(runs) == 2


@pytest.mark.asyncio
async def test_drive_sync_lease_blocks_scheduled_sync_without_losing_event(settings, monkeypatch):
    configured = drive_settings(settings)
    store = PmtStore(configured.pmt_db_path)
    store.initialize()
    channel_id, token = _channel(store, configured)
    store.record_drive_notification(channel_id, token_hash(token), RESOURCE_ID, 2, "update")
    with store._transaction() as db:
        due = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        db.execute(
            "UPDATE drive_notification_events SET available_at=?,next_attempt_at=?", (due, due)
        )
    entered = asyncio.Event()
    release = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        entered.set()
        await release.wait()
        return httpx.Response(
            200,
            text="No,Task Description,Dev,Dev Status\n1,Drive task,Farhan,To-Do\n",
            request=request,
        )

    monkeypatch.setattr("mcp_transfer_node.pmt_drive._access_token", fake_sheet_token)
    monkeypatch.setattr(
        "mcp_transfer_node.pmt_drive.sync_google_sheet",
        lambda *args, **kwargs: sync_google_sheet(
            *args, **kwargs, transport=httpx.MockTransport(handler)
        ),
    )
    drive_run = asyncio.create_task(run_due_drive_events(store, configured, "drive-worker"))
    await entered.wait()
    with pytest.raises(SheetSyncBusy):
        await sync_google_sheet(
            store,
            {"csv_url": CSV_URL},
            actor="scheduled-worker",
            transport=httpx.MockTransport(handler),
        )
    release.set()
    assert (await drive_run)["status"] == "succeeded"
    with store._connect() as db:
        assert db.execute("SELECT status FROM drive_notification_events").fetchone()["status"] == (
            "succeeded"
        )


def test_drive_history_retention_prunes_old_terminal_rows(settings):
    configured = drive_settings(settings)
    store = PmtStore(configured.pmt_db_path)
    store.initialize()
    channel_id, token = _channel(store, configured)
    store.record_drive_notification(channel_id, token_hash(token), RESOURCE_ID, 1, "sync")
    store.stop_drive_channel(channel_id)
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    with store._transaction() as db:
        db.execute("UPDATE drive_notification_events SET finished_at=?,received_at=?", (old, old))
        db.execute(
            """UPDATE drive_watch_channels SET updated_at=?,cleanup_status='succeeded'
                WHERE channel_id=?""",
            (old, channel_id),
        )
    second_id = "32345678-1234-4123-8123-123456789abc"
    second_token = derive_channel_token(configured.pmt_drive_webhook_secret, second_id, FILE_ID)
    store.create_pending_drive_channel(
        second_id,
        FILE_ID,
        token_hash(second_token),
        datetime.now(timezone.utc) + timedelta(hours=1),
    )
    store.bind_drive_channel(
        second_id, "resource-second", RESOURCE_URI, datetime.now(timezone.utc) + timedelta(hours=1)
    )
    store.stop_drive_channel(second_id)
    with store._transaction() as db:
        db.execute(
            """UPDATE drive_watch_channels SET updated_at=?,cleanup_status='succeeded'
                WHERE channel_id=?""",
            ((datetime.now(timezone.utc) - timedelta(days=39)).isoformat(), second_id),
        )
    result = store.prune_drive_history(retention_days=30, keep_channels=1)
    assert result == {"events": 1, "channels": 1}
