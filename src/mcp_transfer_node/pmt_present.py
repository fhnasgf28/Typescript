from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit

MAX_WEB_CONTEXT_CHARS_PER_DOCUMENT = 20_000
MAX_WEB_CONTEXT_CHARS_PER_TAB = 5_000
MAX_RENDER_BLOCKS_PER_TAB = 200
MAX_RENDER_LIST_ITEMS_PER_TAB = 100
MAX_RENDER_TABLE_ROWS = 50
MAX_RENDER_TABLE_COLUMNS = 20
MAX_RENDER_TABLE_CELLS_PER_TAB = 500
MAX_RENDER_LINKS_PER_TAB = 100

_HEADING_RE = re.compile(r"^(?P<marks>#{1,6})\s+(?P<text>.+)$")
_BULLET_RE = re.compile(r"^(?P<indent>\s*)[-*]\s+(?P<text>.+)$")
_ORDERED_RE = re.compile(r"^(?P<indent>\s*)\d+[.)]\s+(?P<text>.+)$")
_LINK_RE = re.compile(r"<(?P<url>[^<>\s]+)>")


def _safe_http_url(value: str) -> str | None:
    if not value or len(value) > 2048:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return value


def _inline_segments(text: str, state: dict[str, int]) -> list[dict[str, str]]:
    segments: list[dict[str, str]] = []
    cursor = 0
    for match in _LINK_RE.finditer(text):
        safe_url = _safe_http_url(match.group("url"))
        if safe_url is None or state["links"] >= MAX_RENDER_LINKS_PER_TAB:
            continue
        if match.start() > cursor:
            segments.append({"text": text[cursor : match.start()]})
        segments.append({"text": safe_url, "url": safe_url})
        state["links"] += 1
        cursor = match.end()
    if cursor < len(text):
        segments.append({"text": text[cursor:]})
    return segments or [{"text": text}]


def _table_cells(line: str) -> list[str] | None:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    cells = [cell.strip() for cell in stripped[1:-1].split("|")]
    if len(cells) < 2:
        return None
    return cells


def _cell_model(text: str, state: dict[str, int]) -> dict[str, Any]:
    parts = [part.strip() for part in text.split(" / ")]
    return {"lines": [_inline_segments(part, state) for part in parts if part] or [[{"text": ""}]]}


def _bounded_render_blocks(text: str) -> tuple[list[dict[str, Any]], bool]:
    lines = text.splitlines()
    blocks: list[dict[str, Any]] = []
    current_list: dict[str, Any] | None = None
    state = {"links": 0, "list_items": 0, "table_cells": 0}
    render_truncated = False

    def append_block(block: dict[str, Any]) -> bool:
        nonlocal render_truncated
        if len(blocks) >= MAX_RENDER_BLOCKS_PER_TAB:
            render_truncated = True
            return False
        blocks.append(block)
        return True

    def flush_list() -> bool:
        nonlocal current_list
        if current_list is None:
            return True
        block = current_list
        current_list = None
        return append_block(block)

    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            if not flush_list():
                break
            index += 1
            continue

        table_cells = _table_cells(line)
        if table_cells is not None:
            if not flush_list():
                break
            rows: list[list[dict[str, Any]]] = []
            table_limited = False
            while index < len(lines):
                row_cells = _table_cells(lines[index])
                if row_cells is None:
                    break
                if len(rows) >= MAX_RENDER_TABLE_ROWS:
                    table_limited = True
                    index += 1
                    continue
                bounded_cells = row_cells[:MAX_RENDER_TABLE_COLUMNS]
                if len(bounded_cells) < len(row_cells):
                    table_limited = True
                available = MAX_RENDER_TABLE_CELLS_PER_TAB - state["table_cells"]
                if available <= 0:
                    table_limited = True
                    index += 1
                    continue
                bounded_cells = bounded_cells[:available]
                if len(bounded_cells) < len(row_cells):
                    table_limited = True
                state["table_cells"] += len(bounded_cells)
                rows.append([_cell_model(cell, state) for cell in bounded_cells])
                index += 1
            if rows:
                append_block(
                    {
                        "kind": "table",
                        "header": rows[0],
                        "rows": rows[1:],
                        "truncated": table_limited,
                    }
                )
            if table_limited:
                render_truncated = True
            if len(blocks) >= MAX_RENDER_BLOCKS_PER_TAB:
                break
            continue

        heading = _HEADING_RE.match(stripped)
        if heading is not None:
            if not flush_list():
                break
            append_block(
                {
                    "kind": "heading",
                    "level": len(heading.group("marks")),
                    "segments": _inline_segments(heading.group("text"), state),
                }
            )
            index += 1
            continue

        list_match = _ORDERED_RE.match(line) or _BULLET_RE.match(line)
        if list_match is not None:
            ordered = _ORDERED_RE.match(line) is not None
            kind = "ordered_list" if ordered else "bullet_list"
            if current_list is not None and current_list["kind"] != kind:
                if not flush_list():
                    break
            if current_list is None:
                current_list = {"kind": kind, "items": []}
            if state["list_items"] >= MAX_RENDER_LIST_ITEMS_PER_TAB:
                render_truncated = True
            else:
                indent = len(list_match.group("indent").replace("\t", "  "))
                current_list["items"].append(
                    {
                        "depth": min(indent // 2, 8),
                        "segments": _inline_segments(list_match.group("text"), state),
                    }
                )
                state["list_items"] += 1
            index += 1
            continue

        if not flush_list():
            break
        if stripped == "---":
            append_block({"kind": "divider"})
        elif stripped in {"[page break]", "[column break]"}:
            append_block({"kind": "divider", "label": stripped[1:-1]})
        else:
            append_block({"kind": "paragraph", "segments": _inline_segments(stripped, state)})
        index += 1

    flush_list()
    return blocks, render_truncated


def build_bounded_web_context(
    document: Mapping[str, Any],
    *,
    max_document_chars: int = MAX_WEB_CONTEXT_CHARS_PER_DOCUMENT,
    max_tab_chars: int = MAX_WEB_CONTEXT_CHARS_PER_TAB,
) -> dict[str, Any]:
    """Build a bounded, autoescape-safe presentation model for one stored snapshot."""
    remaining = max(0, max_document_chars)
    bounded_tabs: list[dict[str, Any]] = []
    raw_tabs = document.get("tabs", [])
    if not isinstance(raw_tabs, Sequence) or isinstance(raw_tabs, (str, bytes)):
        raw_tabs = []

    for raw_tab in raw_tabs:
        if not isinstance(raw_tab, Mapping):
            continue
        tab = dict(raw_tab)
        text = str(tab.pop("text", ""))
        allowance = min(max(0, max_tab_chars), remaining)
        display_text = text[:allowance]
        remaining -= len(display_text)
        render_blocks, render_truncated = _bounded_render_blocks(display_text)
        tab.pop("paragraphs", None)
        tab.pop("tables", None)
        tab.pop("resources", None)
        tab["render_blocks"] = render_blocks
        tab["render_char_count"] = len(display_text)
        tab["display_truncated"] = len(display_text) < len(text) or render_truncated
        bounded_tabs.append(tab)

    selected_tab_id = str(document.get("selected_tab_id", ""))
    selected = next(
        (tab for tab in bounded_tabs if str(tab.get("tab_id", "")) == selected_tab_id),
        bounded_tabs[0] if bounded_tabs else None,
    )
    result = dict(document)
    result["tabs"] = bounded_tabs
    result["selected_tab_title"] = str(selected.get("title", "")) if selected else ""
    result["selected_tab_path"] = str(selected.get("path", "")) if selected else ""
    return result
