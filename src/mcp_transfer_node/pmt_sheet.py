from __future__ import annotations

import csv
import io
from typing import Any
from urllib.parse import urlparse

import httpx

from mcp_transfer_node.pmt_store import PmtStore, TaskInput

ALLOWED_SHEET_HOSTS = {"docs.google.com"}
HEADER_ALIASES = {
    "developer": ("dev", "developer", "assignee"),
    "status": ("dev status", "developer status"),
    "title": ("task", "task description", "description", "issue", "bug description"),
    "menu": ("menu", "menu name", "feature"),
    "module": ("module", "addons", "app"),
    "attachment": ("attachment", "attachment link", "evidence"),
}


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
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_SHEET_HOSTS:
        raise ValueError("sheet URL must use HTTPS on docs.google.com")
    return url


async def sync_google_sheet(
    store: PmtStore,
    payload: dict[str, Any],
    *,
    actor: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    url = validate_sheet_url(str(payload.get("csv_url", "")))
    assignee = str(payload.get("assignee", "Farhan"))
    dev_status = str(payload.get("dev_status", "To-Do"))
    timeout = float(payload.get("timeout_seconds", 30))
    timeout = max(3, min(timeout, 60))
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=True, transport=transport
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
    parsed = parse_google_sheet_tasks(response.text, assignee=assignee, dev_status=dev_status)
    imported: list[str] = []
    existing: list[str] = []
    for row in parsed:
        external_id = f"sheet:{row['sheet_row']}"
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
    }
