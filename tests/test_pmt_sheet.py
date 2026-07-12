from __future__ import annotations

import httpx
import pytest

from mcp_transfer_node.pmt_sheet import (
    parse_google_sheet_tasks,
    sheet_source_id,
    sync_google_sheet,
    validate_sheet_url,
)
from mcp_transfer_node.pmt_store import PmtStore


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
