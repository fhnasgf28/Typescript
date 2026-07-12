from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from mcp_transfer_node.pmt_sheet import (
    parse_google_sheet_tasks,
    SheetSyncBusy,
    sheet_source_id,
    sync_google_sheet,
    validate_sheet_url,
)
from mcp_transfer_node.pmt_store import LeaseExpiredError, PmtStore


SHEET_CSV = """Bug Tracker All,,,,\nNo,Menu,Task Description,Module,Dev,Dev Status,Attachment\n1,Employee,Fix access right,core_hr,Farhan,To-Do,https://example.test/evidence\n2,Loan,Already done,core_loan,Farhan,Done dev,\n3,Overtime,Other developer,core_hr,Yusuf,To-Do,\n"""


def test_parse_google_sheet_filters_farhan_todo_rows():
    tasks = parse_google_sheet_tasks(SHEET_CSV, assignee="Farhan", dev_status="To-Do")

    assert tasks == [
        {
            "sheet_row": "3",
            "title": "Fix access right",
            "menu": "Employee",
            "module": "core_hr",
            "attachment": "https://example.test/evidence",
            "assignee": "Farhan",
            "dev_status": "To-Do",
        }
    ]


def test_validate_sheet_url_rejects_ssrf_targets():
    with pytest.raises(ValueError, match="docs.google.com"):
        validate_sheet_url("https://127.0.0.1/private")


def test_sheet_source_id_separates_spreadsheet_and_gid():
    first = sheet_source_id(
        "https://docs.google.com/spreadsheets/d/sheet-a/export?format=csv&gid=12"
    )
    second = sheet_source_id(
        "https://docs.google.com/spreadsheets/d/sheet-b/export?format=csv&gid=12"
    )

    assert first == "sheet-a:12"
    assert second == "sheet-b:12"


@pytest.mark.asyncio
async def test_sync_google_sheet_imports_once(settings):
    store = PmtStore(settings.pmt_db_path)
    store.initialize()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=SHEET_CSV, request=request)

    payload = {
        "csv_url": "https://docs.google.com/spreadsheets/d/example/export?format=csv",
        "assignee": "Farhan",
        "dev_status": "To-Do",
    }
    transport = httpx.MockTransport(handler)

    first = await sync_google_sheet(store, payload, actor="worker", transport=transport)
    second = await sync_google_sheet(store, payload, actor="worker", transport=transport)

    assert first["imported"] == ["PMT-0001"]
    assert second["imported"] == []
    assert second["already_present"] == ["PMT-0001"]


@pytest.mark.asyncio
async def test_sync_google_sheet_rejects_redirect_even_from_google(settings):
    store = PmtStore(settings.pmt_db_path)
    store.initialize()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"location": "http://127.0.0.1/private"},
            request=request,
        )

    with pytest.raises(ValueError, match="redirects are not allowed"):
        await sync_google_sheet(
            store,
            {"csv_url": "https://docs.google.com/spreadsheets/d/example/export?format=csv"},
            actor="worker",
            transport=httpx.MockTransport(handler),
        )


@pytest.mark.asyncio
async def test_sync_google_sheet_rejects_oversized_response(settings):
    store = PmtStore(settings.pmt_db_path)
    store.initialize()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"x" * (5 * 1024 * 1024 + 1),
            headers={"content-type": "text/csv"},
            request=request,
        )

    with pytest.raises(ValueError, match="exceeds 5 MB"):
        await sync_google_sheet(
            store,
            {"csv_url": "https://docs.google.com/spreadsheets/d/example/export?format=csv"},
            actor="worker",
            transport=httpx.MockTransport(handler),
        )


@pytest.mark.asyncio
async def test_sync_google_sheet_does_not_collide_between_spreadsheets(settings):
    store = PmtStore(settings.pmt_db_path)
    store.initialize()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=SHEET_CSV, request=request)

    transport = httpx.MockTransport(handler)
    first = await sync_google_sheet(
        store,
        {"csv_url": "https://docs.google.com/spreadsheets/d/sheet-a/export?format=csv"},
        actor="worker",
        transport=transport,
    )
    second = await sync_google_sheet(
        store,
        {"csv_url": "https://docs.google.com/spreadsheets/d/sheet-b/export?format=csv"},
        actor="worker",
        transport=transport,
    )

    assert first["imported"] == ["PMT-0001"]
    assert second["imported"] == ["PMT-0002"]
    assert len(store.list_tasks()) == 2


@pytest.mark.asyncio
async def test_shared_lease_blocks_concurrent_schedule_and_manual_fetch(settings):
    store = PmtStore(settings.pmt_db_path)
    store.initialize()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        entered.set()
        await release.wait()
        return httpx.Response(200, text=SHEET_CSV, request=request)

    first = asyncio.create_task(
        sync_google_sheet(
            store,
            {"csv_url": "https://docs.google.com/spreadsheets/d/sheet-a/export?format=csv"},
            actor="schedule-worker",
            transport=httpx.MockTransport(handler),
        )
    )
    await entered.wait()
    with pytest.raises(SheetSyncBusy):
        await sync_google_sheet(
            store,
            {"csv_url": "https://docs.google.com/spreadsheets/d/sheet-b/export?format=csv"},
            actor="manual-worker",
            transport=httpx.MockTransport(handler),
        )
    release.set()
    assert (await first)["imported"] == ["PMT-0001"]


@pytest.mark.asyncio
async def test_stale_sync_run_is_fenced_before_import(settings):
    store = PmtStore(settings.pmt_db_path)
    store.initialize()

    def handler(request: httpx.Request) -> httpx.Response:
        with store._transaction() as db:
            db.execute(
                "UPDATE sheet_sync_leases SET lock_expires_at=?",
                ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),),
            )
        return httpx.Response(200, text=SHEET_CSV, request=request)

    with pytest.raises(LeaseExpiredError):
        await sync_google_sheet(
            store,
            {"csv_url": "https://docs.google.com/spreadsheets/d/example/export?format=csv"},
            actor="stale-worker",
            transport=httpx.MockTransport(handler),
        )
    assert store.list_tasks() == []


@pytest.mark.asyncio
async def test_sheet_bearer_is_pinned_to_exact_origin_and_never_forwarded(settings):
    store = PmtStore(settings.pmt_db_path)
    store.initialize()
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((str(request.url), request.headers.get("authorization")))
        return httpx.Response(
            302, headers={"location": "https://evil.test/private"}, request=request
        )

    with pytest.raises(ValueError, match="redirects are not allowed"):
        await sync_google_sheet(
            store,
            {"csv_url": "https://docs.google.com/spreadsheets/d/example/export?format=csv"},
            actor="drive-worker",
            bearer_token="private-access-token",
            transport=httpx.MockTransport(handler),
        )
    assert requests == [
        (
            "https://docs.google.com/spreadsheets/d/example/export?format=csv",
            "Bearer private-access-token",
        )
    ]

    seen = []

    def public_handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, text=SHEET_CSV, request=request)

    await sync_google_sheet(
        store,
        {"csv_url": "https://docs.google.com/spreadsheets/d/example/export?format=csv"},
        actor="manual-worker",
        transport=httpx.MockTransport(public_handler),
    )
    assert seen == [None]
