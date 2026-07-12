from __future__ import annotations

from copy import deepcopy

from mcp_transfer_node.pmt_present import (
    MAX_RENDER_BLOCKS_PER_TAB,
    MAX_RENDER_LIST_ITEMS_PER_TAB,
    MAX_RENDER_TABLE_CELLS_PER_TAB,
    MAX_RENDER_TABLE_ROWS,
    build_bounded_web_context,
)


def _document(texts: list[str], *, selected_tab_id: str = "tab-0") -> dict:
    tabs = [
        {
            "tab_id": f"tab-{index}",
            "parent_tab_id": None,
            "depth": 0,
            "position": index,
            "position_path": [index],
            "path": f"Tab {index}",
            "title": f"Tab {index}",
            "text": text,
            "char_count": len(text),
            "paragraphs": [],
            "tables": [],
            "resources": {"ignored": True},
        }
        for index, text in enumerate(texts)
    ]
    return {
        "id": "context-1",
        "title": "Legacy snapshot",
        "selected_tab_id": selected_tab_id,
        "content_sha256": "a" * 64,
        "context_version": 7,
        "tabs": tabs,
    }


def test_semantic_blocks_preserve_order_and_only_link_safe_http_urls() -> None:
    text = "\n".join(
        [
            "## Access policy",
            "Read the source <https://example.test/spec>.",
            "- First check",
            "1. First numbered step",
            "| Role | Access |",
            "| Supervisor / Regional | <script>alert(1)</script> |",
            "Unsafe <javascript:alert(1)>",
            "---",
        ]
    )

    rendered = build_bounded_web_context(_document([text]))
    tab = rendered["tabs"][0]

    assert [block["kind"] for block in tab["render_blocks"]] == [
        "heading",
        "paragraph",
        "bullet_list",
        "ordered_list",
        "table",
        "paragraph",
        "divider",
    ]
    assert "display_text" not in tab
    assert "paragraphs" not in tab
    assert "tables" not in tab
    assert "resources" not in tab
    safe_segments = tab["render_blocks"][1]["segments"]
    assert [segment["url"] for segment in safe_segments if "url" in segment] == [
        "https://example.test/spec"
    ]
    unsafe_segments = tab["render_blocks"][5]["segments"]
    assert all("url" not in segment for segment in unsafe_segments)
    table = tab["render_blocks"][4]
    assert table["header"][0]["lines"][0][0]["text"] == "Role"
    assert len(table["rows"][0][0]["lines"]) == 2


def test_render_limits_bound_blocks_lists_tables_and_cells() -> None:
    paragraphs = "\n".join(f"Paragraph {index}" for index in range(260))
    bullets = "\n".join(f"- Item {index}" for index in range(140))
    table = "\n".join("| " + " | ".join("x" for _ in range(12)) + " |" for _ in range(80))

    rendered = build_bounded_web_context(_document([paragraphs, bullets, table]))

    paragraph_tab, list_tab, table_tab = rendered["tabs"]
    assert len(paragraph_tab["render_blocks"]) == MAX_RENDER_BLOCKS_PER_TAB
    assert paragraph_tab["display_truncated"] is True

    list_items = sum(
        len(block["items"])
        for block in list_tab["render_blocks"]
        if block["kind"] in {"bullet_list", "ordered_list"}
    )
    assert list_items == MAX_RENDER_LIST_ITEMS_PER_TAB
    assert list_tab["display_truncated"] is True

    table_blocks = [block for block in table_tab["render_blocks"] if block["kind"] == "table"]
    assert len(table_blocks) == 1
    table_block = table_blocks[0]
    table_rows = 1 + len(table_block["rows"])
    table_cells = len(table_block["header"]) + sum(len(row) for row in table_block["rows"])
    assert table_rows <= MAX_RENDER_TABLE_ROWS
    assert table_cells == MAX_RENDER_TABLE_CELLS_PER_TAB
    assert table_block["truncated"] is True
    assert table_tab["display_truncated"] is True


def test_document_and_tab_character_budgets_remain_enforced() -> None:
    rendered = build_bounded_web_context(
        _document(["x" * 6_000, "y" * 5_000, "z" * 5_000]),
        max_document_chars=7_000,
        max_tab_chars=5_000,
    )

    assert [tab["render_char_count"] for tab in rendered["tabs"]] == [5_000, 2_000, 0]
    assert all(tab["display_truncated"] for tab in rendered["tabs"])


def test_legacy_snapshot_fallback_is_readable_and_presentation_is_non_mutating() -> None:
    document = _document(
        ["# Legacy heading\nLegacy paragraph\n| Name | Value |\n| One | Two |"],
        selected_tab_id="tab-0",
    )
    original = deepcopy(document)

    rendered = build_bounded_web_context(document)

    assert document == original
    assert rendered["content_sha256"] == document["content_sha256"]
    assert rendered["context_version"] == document["context_version"]
    assert rendered["selected_tab_title"] == "Tab 0"
    assert [block["kind"] for block in rendered["tabs"][0]["render_blocks"]] == [
        "heading",
        "paragraph",
        "table",
    ]
