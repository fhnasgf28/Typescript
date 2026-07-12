from __future__ import annotations

import inspect
import os
import stat
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from mcp_transfer_node.config import TransferSettings
from mcp_transfer_node.pmt_gdocs import GoogleDocsError, parse_google_doc_url, read_google_doc
from mcp_transfer_node.pmt_store import PmtStore, TaskInput, derive_google_doc_task_title

GoogleDocsFetcher = Callable[..., dict[str, Any] | Awaitable[dict[str, Any]]]
GOOGLE_DOC_TASK_DESCRIPTION = (
    "Task dibuat dari Google Docs context. Gunakan snapshot terlampir sebagai requirement utama."
)


class GoogleDocsContentChangedError(GoogleDocsError):
    """Raised when confirmation observes a different semantic snapshot hash."""


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
        try:
            result = self.fetcher(
                source_url,
                credential,
                timeout_seconds=self.settings.google_docs_timeout_seconds,
            )
            snapshot = await result if inspect.isawaitable(result) else result
        except GoogleDocsError:
            raise
        except TimeoutError as exc:
            raise GoogleDocsError("Google Docs request timed out") from exc
        except OSError as exc:
            raise GoogleDocsError("Google Docs provider is unavailable") from exc
        except ValueError as exc:
            raise GoogleDocsError("Google Docs provider returned an invalid response") from exc
        except RuntimeError as exc:
            raise GoogleDocsError("Google Docs provider request failed") from exc
        if not isinstance(snapshot, dict):
            raise GoogleDocsError("Google Docs reader returned an invalid snapshot")
        return snapshot

    async def preview(self, source_url: str) -> dict[str, Any]:
        """Validate and fetch a Google Docs snapshot without writing to SQLite."""
        canonical_url = source_url.strip()
        link = parse_google_doc_url(canonical_url)
        try:
            snapshot = await self._fetch(canonical_url)
        except GoogleDocsError:
            raise
        except TimeoutError as exc:
            raise GoogleDocsError("Google Docs request timed out") from exc
        except OSError as exc:
            raise GoogleDocsError("Google Docs provider is unavailable") from exc
        except ValueError as exc:
            raise GoogleDocsError("Google Docs provider returned an invalid response") from exc
        except RuntimeError as exc:
            raise GoogleDocsError("Google Docs provider request failed") from exc
        if snapshot.get("document_id") != link.document_id:
            raise GoogleDocsError(
                "Google Docs response document ID does not match the requested document"
            )
        if link.selected_tab_id and snapshot.get("selected_tab_id") != link.selected_tab_id:
            raise GoogleDocsError("Selected Google Docs tab does not match the requested tab")
        return snapshot

    async def create_task_from_google_doc(
        self,
        data: TaskInput,
        *,
        source_url: str,
        title_override: str,
        actor: str,
        idempotency_key: str,
        expected_content_sha256: str | None = None,
    ) -> dict[str, Any]:
        """Fetch outside SQLite, then atomically create the task and snapshot."""
        replay = self.store.get_google_doc_task_creation(idempotency_key, source_url)
        if replay is not None:
            return replay
        snapshot = await self.preview(source_url)
        if (
            expected_content_sha256 is not None
            and snapshot.get("content_sha256") != expected_content_sha256
        ):
            raise GoogleDocsContentChangedError(
                "Google Docs content changed; preview the document again"
            )
        task_data = replace(
            data,
            title=derive_google_doc_task_title(snapshot, title_override),
            description=data.description.strip() or GOOGLE_DOC_TASK_DESCRIPTION,
            source="google_docs",
        )
        return self.store.create_task_from_google_doc(
            task_data,
            source_url=source_url.strip(),
            snapshot=snapshot,
            actor=actor,
            idempotency_key=idempotency_key,
        )

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
