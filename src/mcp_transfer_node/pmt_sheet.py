from __future__ import annotations

import csv
import io
import asyncio
import uuid
from typing import Any
import re
from urllib.parse import parse_qs, parse_qsl, urlparse

import httpx

from mcp_transfer_node.pmt_store import PmtStore, TaskInput

ALLOWED_SHEET_HOSTS = {"docs.google.com"}
MAX_SHEET_RESPONSE_BYTES = 5 * 1024 * 1024
ALLOWED_CSV_CONTENT_TYPES = {"text/csv", "text/plain", "application/octet-stream"}
SHEET_SYNC_OPERATION_DEADLINE_SECONDS = 60
_SHEET_PATH_RE = re.compile(r"^/spreadsheets/d/[A-Za-z0-9_-]{1,200}/export/?$")
_SHEET_REDIRECT_HOST_RE = re.compile(r"^[a-z0-9-]{1,120}-sheets\.googleusercontent\.com$")
HEADER_ALIASES = {
    "developer": ("dev", "developer", "assignee"),
    "status": ("dev status", "developer status"),
    "title": ("task", "task description", "description", "issue", "bug description"),
    "menu": ("menu", "menu name", "feature"),
    "module": ("module", "addons", "app"),
    "attachment": ("attachment", "attachment link", "evidence"),
}


class SheetSyncBusy(RuntimeError):
    """Raised when another process owns the durable sync lease for this Sheet."""


def _normalized(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _find_header(rows: list[list[str]]) -> tuple[int, dict[str, int]]:
    for row_index, row in enumerate(rows[:30]):
        normalized = [_normalized(cell) for cell in row]
        mapping: dict[str, int] = {}
        for canonical, aliases in HEADER_ALIASES.items():
            for alias in aliases:
                if alias in normalized:
                    mapping[canonical] = normalized.index(alias)
                    break
        if "status" in mapping and ("title" in mapping or "menu" in mapping):
            return row_index, mapping
    raise ValueError("could not detect a task table header")


def _cell(row: list[str], mapping: dict[str, int], key: str) -> str:
    index = mapping.get(key)
    return row[index].strip() if index is not None and index < len(row) else ""


def parse_google_sheet_tasks(
    csv_text: str,
    *,
    assignee: str = "Farhan",
    dev_status: str = "To-Do",
) -> list[dict[str, str]]:
    rows = list(csv.reader(io.StringIO(csv_text)))
    header_index, mapping = _find_header(rows)
    tasks: list[dict[str, str]] = []
    for sheet_row, row in enumerate(rows[header_index + 1 :], start=header_index + 2):
        developer = _cell(row, mapping, "developer")
        status = _cell(row, mapping, "status")
        if developer and _normalized(developer) != _normalized(assignee):
            continue
        if _normalized(status) != _normalized(dev_status):
            continue
        title = _cell(row, mapping, "title") or _cell(row, mapping, "menu")
        if not title:
            continue
        tasks.append(
            {
                "sheet_row": str(sheet_row),
                "title": title,
                "menu": _cell(row, mapping, "menu"),
                "module": _cell(row, mapping, "module"),
                "attachment": _cell(row, mapping, "attachment"),
                "assignee": developer or assignee,
                "dev_status": status,
            }
        )
    return tasks


def validate_sheet_url(url: str) -> str:
    if not isinstance(url, str) or not url or len(url) > 2048:
        raise ValueError("sheet URL is invalid")
    try:
        parsed = urlparse(url)
        port = parsed.port
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except (TypeError, ValueError) as exc:
        raise ValueError("sheet URL is malformed") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname not in ALLOWED_SHEET_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.fragment
        or _SHEET_PATH_RE.fullmatch(parsed.path) is None
        or len(query) > 2
        or any(key not in {"format", "gid"} for key, _value in query)
        or len({key for key, _value in query}) != len(query)
        or dict(query).get("format", "csv") != "csv"
    ):
        raise ValueError("sheet URL must use HTTPS on docs.google.com")
    return url


def sheet_source_id(url: str) -> str:
    parsed = urlparse(validate_sheet_url(url))
    parts = [part for part in parsed.path.split("/") if part]
    try:
        spreadsheet_id = parts[parts.index("d") + 1]
    except (ValueError, IndexError) as exc:
        raise ValueError("sheet URL must contain a spreadsheet ID") from exc
    if not spreadsheet_id or len(spreadsheet_id) > 200:
        raise ValueError("invalid spreadsheet ID")
    gid = parse_qs(parsed.query).get("gid", ["0"])[0]
    if not gid.isdigit() or len(gid) > 20:
        raise ValueError("invalid sheet gid")
    return f"{spreadsheet_id}:{gid}"


def validate_sheet_export_redirect(url: str, source_url: str) -> str:
    """Validate Google's one-time CSV export URL without forwarding credentials."""
    if not isinstance(url, str) or not url or len(url) > 4096:
        raise ValueError("sheet export redirect is invalid")
    source_id = sheet_source_id(source_url)
    spreadsheet_id, expected_gid = source_id.split(":", 1)
    try:
        parsed = urlparse(url)
        port = parsed.port
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except (TypeError, ValueError) as exc:
        raise ValueError("sheet export redirect is malformed") from exc
    path_parts = [part for part in parsed.path.split("/") if part]
    query_values = dict(query)
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or _SHEET_REDIRECT_HOST_RE.fullmatch(parsed.hostname) is None
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.fragment
        or not parsed.path.startswith("/export/")
        or not path_parts
        or path_parts[-1] != spreadsheet_id
        or len(path_parts) > 12
        or any(len(part) > 240 for part in path_parts)
        or len(query) > 2
        or any(key not in {"format", "gid"} for key, _value in query)
        or len({key for key, _value in query}) != len(query)
        or query_values.get("format", "csv") != "csv"
        or query_values.get("gid", "0") != expected_gid
    ):
        raise ValueError("sheet export redirect must use Google's bounded export endpoint")
    return url


async def sync_google_sheet(
    store: PmtStore,
    payload: dict[str, Any],
    *,
    actor: str,
    transport: httpx.AsyncBaseTransport | None = None,
    bearer_token: str | None = None,
) -> dict[str, Any]:
    url = validate_sheet_url(str(payload.get("csv_url", "")))
    source_id = sheet_source_id(url)
    assignee = str(payload.get("assignee", "Farhan"))
    dev_status = str(payload.get("dev_status", "To-Do"))
    timeout = float(payload.get("timeout_seconds", 30))
    timeout = max(3, min(timeout, 60))
    if bearer_token is not None and (
        not isinstance(bearer_token, str)
        or not bearer_token
        or any(char.isspace() for char in bearer_token)
    ):
        raise ValueError("Sheet bearer token is invalid")
    run_id = f"sheet_sync_{uuid.uuid4().hex}"
    lease_key = "google_sheet_sync"
    if not store.claim_sheet_sync_lease(lease_key, actor, run_id):
        raise SheetSyncBusy("Google Sheet sync is already in progress")
    try:
        async with asyncio.timeout(SHEET_SYNC_OPERATION_DEADLINE_SECONDS):
            content = bytearray()
            headers = {"Accept": "text/csv,text/plain;q=0.9"}
            # validate_sheet_url pins the only origin which may receive this credential.
            if bearer_token is not None:
                headers["Authorization"] = f"Bearer {bearer_token}"
            async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=False, transport=transport
            ) as client:
                request_url = url
                request_headers = headers
                for request_number in range(2):
                    async with client.stream(
                        "GET", request_url, headers=request_headers
                    ) as response:
                        if response.is_redirect:
                            if request_number != 0:
                                raise ValueError("additional sheet redirects are not allowed")
                            redirect_url = validate_sheet_export_redirect(
                                response.headers.get("location", ""), url
                            )
                            request_url = redirect_url
                            # The bearer credential is pinned to docs.google.com. Google's
                            # bounded one-time export URL is fetched in a separate request
                            # with no Authorization header.
                            request_headers = {"Accept": "text/csv,text/plain;q=0.9"}
                            continue
                        response.raise_for_status()
                        if request_number == 0:
                            if validate_sheet_url(str(response.url)) != url:
                                raise ValueError("sheet response URL changed unexpectedly")
                        elif str(response.url) != request_url:
                            raise ValueError("sheet redirect response URL changed unexpectedly")
                        content_type = (
                            response.headers.get("content-type", "")
                            .split(";", 1)[0]
                            .strip()
                            .lower()
                        )
                        if content_type and content_type not in ALLOWED_CSV_CONTENT_TYPES:
                            raise ValueError("sheet response must be CSV or plain text")
                        async for chunk in response.aiter_bytes():
                            content.extend(chunk)
                            if len(content) > MAX_SHEET_RESPONSE_BYTES:
                                raise ValueError("sheet response exceeds 5 MB")
                        break
                else:  # pragma: no cover - the bounded loop always breaks or raises
                    raise ValueError("sheet export did not return content")
            csv_text = bytes(content).decode("utf-8-sig")
            parsed = parse_google_sheet_tasks(csv_text, assignee=assignee, dev_status=dev_status)
            imported: list[str] = []
            existing: list[str] = []
            for row in parsed:
                # Run fencing immediately before every durable mutation prevents a stale
                # network worker from importing after its lease has been reclaimed.
                store.assert_sheet_sync_lease(lease_key, actor, run_id)
                external_id = f"sheet:{source_id}:row:{row['sheet_row']}"
                before = store.get_external_task("google_sheet", external_id)
                task = store.create_task(
                    TaskInput(
                        title=row["title"],
                        description=(
                            f"Imported from Google Sheet row {row['sheet_row']}"
                            + (f"\nAttachment: {row['attachment']}" if row["attachment"] else "")
                        ),
                        project=str(payload.get("project", "HMX")),
                        module=row["module"],
                        menu=row["menu"],
                        source="google_sheet",
                        external_id=external_id,
                        assignee=row["assignee"],
                        priority=str(payload.get("priority", "normal")),
                        target_branch=str(payload.get("target_branch", "Human-Resources")),
                    ),
                    actor=actor,
                )
                (existing if before else imported).append(task["task_key"])
            return {
                "matched": len(parsed),
                "imported": imported,
                "already_present": existing,
                "filter": {"assignee": assignee, "dev_status": dev_status},
                "source_id": source_id,
            }
    finally:
        store.release_sheet_sync_lease(lease_key, actor, run_id)
