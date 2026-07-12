from __future__ import annotations

import inspect
import os
import stat
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from mcp_transfer_node.config import TransferSettings
from mcp_transfer_node.pmt_gdocs import GoogleDocsError, parse_google_doc_url, read_google_doc
from mcp_transfer_node.pmt_store import PmtStore

GoogleDocsFetcher = Callable[..., dict[str, Any] | Awaitable[dict[str, Any]]]


class GoogleDocsContextService:
    """Coordinates bounded Google Docs fetches without holding SQLite locks."""

    def __init__(
        self,
        store: PmtStore,
        settings: TransferSettings,
        *,
        fetcher: GoogleDocsFetcher = read_google_doc,
    ) -> None:
        self.store = store
        self.settings = settings
        self.fetcher = fetcher

    def _credential(self) -> Path:
        path = self.settings.google_docs_service_account_file
        if path is None:
            raise GoogleDocsError("Google Docs context integration is not configured")
        try:
            metadata = path.stat()
        except OSError as exc:
            raise GoogleDocsError("Google Docs context integration is not available") from exc
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_mode & 0o077
        ):
            raise GoogleDocsError(
                "Google Docs service-account credential must be an owner-owned, owner-only regular file"
            )
        return path

    async def _fetch(self, source_url: str) -> dict[str, Any]:
        credential = self._credential()
        result = self.fetcher(
            source_url,
            credential,
            timeout_seconds=self.settings.google_docs_timeout_seconds,
        )
        snapshot = await result if inspect.isawaitable(result) else result
        if not isinstance(snapshot, dict):
            raise GoogleDocsError("Google Docs reader returned an invalid snapshot")
        return snapshot

    async def attach(
        self,
        task_ref: str,
        source_url: str,
        *,
        actor: str,
        expected_version: int,
        expected_owner: str | None = None,
        expected_run_id: str | None = None,
    ) -> dict[str, Any]:
        link = parse_google_doc_url(source_url)
        preflight = self.store.check_context_write_access(
            task_ref,
            expected_version=expected_version,
            expected_owner=expected_owner,
            expected_run_id=expected_run_id,
            external_id=link.document_id,
            source_url=source_url,
        )
        if preflight["context"] is not None:
            return {**preflight["context"], "changed": False}
        snapshot = await self._fetch(source_url)
        return self.store.save_task_context_snapshot(
            task_ref,
            source_url=source_url,
            snapshot=snapshot,
            actor=actor,
            operation="attach",
            expected_version=expected_version,
            expected_owner=expected_owner,
            expected_run_id=expected_run_id,
        )

    async def refresh(
        self,
        task_ref: str,
        context_ref: str,
        *,
        actor: str,
        expected_version: int,
        expected_context_version: int,
        expected_owner: str | None = None,
        expected_run_id: str | None = None,
    ) -> dict[str, Any]:
        preflight = self.store.check_context_write_access(
            task_ref,
            expected_version=expected_version,
            expected_owner=expected_owner,
            expected_run_id=expected_run_id,
            context_ref=context_ref,
        )
        context = preflight["context"]
        source_url = context["source_url"]
        parse_google_doc_url(source_url)
        snapshot = await self._fetch(source_url)
        return self.store.save_task_context_snapshot(
            task_ref,
            source_url=source_url,
            snapshot=snapshot,
            actor=actor,
            operation="refresh",
            context_ref=context["id"],
            expected_version=expected_version,
            expected_context_version=expected_context_version,
            expected_owner=expected_owner,
            expected_run_id=expected_run_id,
        )
