from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import inspect
import json
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, quote, urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response

from mcp_transfer_node.config import TransferSettings
from mcp_transfer_node.pmt_gdocs import GoogleDocsError, get_google_access_token
from mcp_transfer_node.pmt_sheet import SheetSyncBusy, sync_google_sheet
from mcp_transfer_node.pmt_store import PmtStore

DRIVE_METADATA_SCOPE = "https://www.googleapis.com/auth/drive.metadata.readonly"
SHEETS_READONLY_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
DRIVE_API_ORIGIN = "https://www.googleapis.com"
CHANNELS_STOP_ENDPOINT = f"{DRIVE_API_ORIGIN}/drive/v3/channels/stop"
CALLBACK_PATH = "/api/v1/pmt/drive-notifications/bug-tracker"
MAX_DRIVE_RESPONSE_BYTES = 64 * 1024
WATCH_TTL = timedelta(hours=23, minutes=50)
CHANNEL_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
RESOURCE_RE = re.compile(r"^[A-Za-z0-9_.:/=-]{1,512}$")
MESSAGE_RE = re.compile(r"^[1-9][0-9]{0,19}$")
CHANGED_RE = re.compile(r"^[a-z]+(?:,[a-z]+)*$")
_ACTIVE_TASKS: dict[str, asyncio.Task[Any]] = {}


class DriveWatchError(ValueError):
    """Safe Drive watch error which never contains provider payloads or credentials."""


TokenProvider = Callable[[Any, tuple[str, ...]], str | Awaitable[str]]


def derive_channel_token(secret: str, channel_id: str, file_id: str) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        f"pmt-drive-watch-v1\0{channel_id}\0{file_id}".encode(),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _watch_endpoint(file_id: str) -> str:
    return f"{DRIVE_API_ORIGIN}/drive/v3/files/{quote(file_id, safe='')}/watch"


def _validate_resource_uri(uri: str, file_id: str) -> str:
    if not isinstance(uri, str) or len(uri) > 2048:
        raise DriveWatchError("Google Drive returned an invalid resource URI")
    parsed = urlsplit(uri)
    expected_path = f"/drive/v3/files/{quote(file_id, safe='')}"
    try:
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise DriveWatchError("Google Drive returned an invalid resource URI") from exc
    query_values = dict(query)
    query_is_valid = not query or (
        len(query) == 2
        and len(query_values) == 2
        and query_values == {"alt": "json", "supportsAllDrives": "true"}
    )
    if (
        parsed.scheme != "https"
        or parsed.hostname != "www.googleapis.com"
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != expected_path
        or not query_is_valid
        or parsed.fragment
    ):
        raise DriveWatchError("Google Drive returned an unexpected resource URI")
    return uri


async def _access_token(
    settings: TransferSettings,
    provider: TokenProvider | None,
    scopes: tuple[str, ...] = (DRIVE_METADATA_SCOPE,),
) -> str:
    credential = settings.google_docs_service_account_file
    if credential is None:
        raise DriveWatchError("Google service-account credential is not configured")
    try:
        result = (
            provider(credential, scopes)
            if provider
            else get_google_access_token(credential, scopes, settings.google_docs_timeout_seconds)
        )
        token = await result if inspect.isawaitable(result) else result
    except GoogleDocsError as exc:
        raise DriveWatchError(str(exc)) from exc
    except Exception as exc:
        raise DriveWatchError("Google service-account authentication failed") from exc
    if not isinstance(token, str) or not token or any(char.isspace() for char in token):
        raise DriveWatchError("Google service-account authentication returned an invalid token")
    return token


async def _json_request(
    method: str,
    endpoint: str,
    *,
    token: str,
    payload: dict[str, Any],
    timeout: float,
    transport: httpx.AsyncBaseTransport | None,
    params: dict[str, str] | None,
) -> dict[str, Any]:
    body = bytearray()
    try:
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(timeout), follow_redirects=False, transport=transport
            ) as client:
                async with client.stream(
                    method,
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    params=params,
                ) as response:
                    if response.is_redirect:
                        raise DriveWatchError("Google Drive API redirects are not allowed")
                    if str(response.url).split("?", 1)[0] != endpoint:
                        raise DriveWatchError("Google Drive API returned an unexpected endpoint")
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_DRIVE_RESPONSE_BYTES:
                            raise DriveWatchError("Google Drive API response is too large")
                    if response.status_code >= 400:
                        if response.status_code in {401, 403}:
                            raise DriveWatchError("Google Drive API denied the watch request")
                        if response.status_code == 404:
                            raise DriveWatchError("Google Drive file was not found")
                        if response.status_code == 429:
                            raise DriveWatchError("Google Drive API rate limit was reached")
                        raise DriveWatchError("Google Drive API request failed")
                    if not body:
                        return {}
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    if content_type != "application/json":
                        raise DriveWatchError("Google Drive API response is not JSON")
    except TimeoutError as exc:
        raise DriveWatchError("Google Drive API request timed out") from exc
    except DriveWatchError:
        raise
    except (httpx.HTTPError, OSError) as exc:
        raise DriveWatchError("Google Drive API request failed") from exc
    try:
        value = json.loads(bytes(body))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise DriveWatchError("Google Drive API returned malformed JSON") from exc
    if not isinstance(value, dict):
        raise DriveWatchError("Google Drive API returned malformed JSON")
    return value


async def stop_remote_channel(
    settings: TransferSettings,
    channel_id: str,
    resource_id: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    access_token_provider: TokenProvider | None = None,
) -> None:
    token = await _access_token(settings, access_token_provider)
    await _json_request(
        "POST",
        CHANNELS_STOP_ENDPOINT,
        token=token,
        payload={"id": channel_id, "resourceId": resource_id},
        params=None,
        timeout=settings.google_docs_timeout_seconds,
        transport=transport,
    )


async def _register_drive_watch_unlocked(
    store: PmtStore,
    settings: TransferSettings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    access_token_provider: TokenProvider | None = None,
) -> dict[str, Any]:
    if not settings.pmt_drive_watch_enabled:
        raise DriveWatchError("Drive watch is disabled")
    channel_id = str(uuid.uuid4())
    channel_token = derive_channel_token(
        settings.pmt_drive_webhook_secret, channel_id, settings.pmt_drive_spreadsheet_id
    )
    requested_expiration = datetime.now(timezone.utc) + WATCH_TTL
    store.create_pending_drive_channel(
        channel_id,
        settings.pmt_drive_spreadsheet_id,
        token_hash(channel_token),
        requested_expiration,
    )
    resource_id_for_cleanup: str | None = None
    try:
        oauth_token = await _access_token(settings, access_token_provider)
        endpoint = _watch_endpoint(settings.pmt_drive_spreadsheet_id)
        result = await _json_request(
            "POST",
            endpoint,
            token=oauth_token,
            payload={
                "id": channel_id,
                "type": "web_hook",
                "address": settings.pmt_drive_webhook_callback_url,
                "token": channel_token,
                "expiration": str(int(requested_expiration.timestamp() * 1000)),
            },
            params={"supportsAllDrives": "true"},
            timeout=settings.google_docs_timeout_seconds,
            transport=transport,
        )
        resource_id = result.get("resourceId")
        if isinstance(resource_id, str) and RESOURCE_RE.fullmatch(resource_id) is not None:
            resource_id_for_cleanup = resource_id
        if result.get("id") != channel_id:
            raise DriveWatchError("Google Drive returned a different channel ID")
        if resource_id_for_cleanup is None:
            raise DriveWatchError("Google Drive returned an invalid resource ID")
        resource_uri = _validate_resource_uri(
            result.get("resourceUri"), settings.pmt_drive_spreadsheet_id
        )
        try:
            expiration_ms = int(result.get("expiration"))
            expiration = datetime.fromtimestamp(expiration_ms / 1000, timezone.utc)
        except (TypeError, ValueError, OverflowError) as exc:
            raise DriveWatchError("Google Drive returned an invalid expiration") from exc
        if expiration <= datetime.now(
            timezone.utc
        ) or expiration > requested_expiration + timedelta(minutes=5):
            raise DriveWatchError("Google Drive returned an unsafe expiration")
        store.bind_drive_channel(channel_id, resource_id, resource_uri, expiration)
    except Exception:
        if resource_id_for_cleanup is not None:
            store.mark_drive_channel_cleanup_needed(channel_id, resource_id_for_cleanup)
            try:
                await stop_remote_channel(
                    settings,
                    channel_id,
                    resource_id_for_cleanup,
                    transport=transport,
                    access_token_provider=access_token_provider,
                )
            except DriveWatchError:
                pass
            else:
                store.record_drive_cleanup_result(channel_id, success=True)
        else:
            # If Google did not provide a usable resource ID the provider stop contract
            # cannot be called. The pending local row is bounded by retention; Google's
            # requested channel expiration remains the remote fallback.
            store.fail_pending_drive_channel(channel_id)
        raise

    old_channels = store.replace_drive_channels(settings.pmt_drive_spreadsheet_id, channel_id)
    stopped = 0
    for old in old_channels:
        try:
            await stop_remote_channel(
                settings,
                old["channel_id"],
                old["resource_id"],
                transport=transport,
                access_token_provider=access_token_provider,
            )
            stopped += 1
            store.record_drive_cleanup_result(old["channel_id"], success=True)
        except DriveWatchError:
            store.record_drive_cleanup_result(
                old["channel_id"], success=False, error_type="DriveWatchError"
            )
    return {
        "channel_id": channel_id,
        "expiration_at": expiration.isoformat(),
        "replaced": len(old_channels),
        "stopped": stopped,
    }


async def register_drive_watch(
    store: PmtStore,
    settings: TransferSettings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    access_token_provider: TokenProvider | None = None,
) -> dict[str, Any]:
    """Replace a watch while holding a durable local registration lease."""
    store.set_drive_watch_desired(settings.pmt_drive_spreadsheet_id, True)
    owner = f"drive_watch_registration_{uuid.uuid4().hex}"
    if not store.claim_drive_watch_lease(settings.pmt_drive_spreadsheet_id, owner):
        raise DriveWatchError("Drive watch registration is already in progress")
    try:
        try:
            result = await _register_drive_watch_unlocked(
                store,
                settings,
                transport=transport,
                access_token_provider=access_token_provider,
            )
        except Exception as exc:
            store.record_drive_renewal_result(
                settings.pmt_drive_spreadsheet_id,
                success=False,
                error_type=type(exc).__name__,
            )
            raise
        store.record_drive_renewal_result(settings.pmt_drive_spreadsheet_id, success=True)
        return result
    finally:
        store.release_drive_watch_lease(settings.pmt_drive_spreadsheet_id, owner)


async def stop_active_drive_watches(
    store: PmtStore,
    settings: TransferSettings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    access_token_provider: TokenProvider | None = None,
) -> dict[str, int]:
    # Persist intent before competing with registration so a worker cannot renew after Stop.
    store.set_drive_watch_desired(settings.pmt_drive_spreadsheet_id, False)
    owner = f"drive_watch_stop_{uuid.uuid4().hex}"
    if not store.claim_drive_watch_lease(settings.pmt_drive_spreadsheet_id, owner):
        raise DriveWatchError("Drive watch update is already in progress")
    try:
        channels = store.drive_watch_status(settings.pmt_drive_spreadsheet_id)["channels"]
        active = [item for item in channels if item["state"] == "active" and item["bound"]]
        stopped = 0
        for item in active:
            row = store.stop_drive_channel(item["channel_id"])
            try:
                await stop_remote_channel(
                    settings,
                    row["channel_id"],
                    row["resource_id"],
                    transport=transport,
                    access_token_provider=access_token_provider,
                )
                stopped += 1
                store.record_drive_cleanup_result(row["channel_id"], success=True)
            except DriveWatchError:
                store.record_drive_cleanup_result(
                    row["channel_id"], success=False, error_type="DriveWatchError"
                )
        return {"marked_stopped": len(active), "remote_stopped": stopped}
    finally:
        store.release_drive_watch_lease(settings.pmt_drive_spreadsheet_id, owner)


def _sync_payload(settings: TransferSettings) -> dict[str, Any]:
    return {
        "csv_url": settings.pmt_drive_csv_url,
        "assignee": settings.pmt_drive_assignee,
        "dev_status": settings.pmt_drive_dev_status,
        "project": settings.pmt_drive_project,
        "target_branch": settings.pmt_drive_target_branch,
        "timeout_seconds": settings.google_docs_timeout_seconds,
    }


async def run_due_drive_events(
    store: PmtStore, settings: TransferSettings, worker_id: str = "drive-webhook"
) -> dict[str, Any]:
    claim = store.claim_drive_events(worker_id)
    if claim is None:
        return {"status": "idle"}
    try:
        bearer_token = await _access_token(settings, None, (SHEETS_READONLY_SCOPE,))
        result = await sync_google_sheet(
            store, _sync_payload(settings), actor=worker_id, bearer_token=bearer_token
        )
    except SheetSyncBusy:
        store.defer_drive_events_busy(claim["run_id"], worker_id)
        return {"status": "busy", "events": len(claim["event_ids"])}
    except Exception as exc:
        safe = {
            "error_type": type(exc).__name__,
            "message": "Drive-triggered Sheet sync failed",
        }
        store.finish_drive_events(claim["run_id"], worker_id, success=False, result=safe)
        return {"status": "failed", "events": len(claim["event_ids"]), "result": safe}
    metadata = {
        "matched": result.get("matched", 0),
        "imported_count": len(result.get("imported", [])),
        "existing_count": len(result.get("already_present", [])),
        "source_id": result.get("source_id", ""),
    }
    store.finish_drive_events(claim["run_id"], worker_id, success=True, result=metadata)
    return {"status": "succeeded", "events": len(claim["event_ids"]), "result": metadata}


async def _debounced_runner(store: PmtStore, settings: TransferSettings) -> None:
    # One durable runner follows late arrivals until the queue is empty. Sleeping until
    # the next due timestamp avoids both the T+4 lost wake-up and a tight polling loop.
    while (delay := store.next_drive_event_delay()) is not None:
        await asyncio.sleep(delay)
        await run_due_drive_events(store, settings)


async def retry_drive_channel_cleanups(
    store: PmtStore,
    settings: TransferSettings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    access_token_provider: TokenProvider | None = None,
) -> dict[str, int]:
    attempted = succeeded = 0
    for row in store.due_drive_channel_cleanups():
        attempted += 1
        try:
            await stop_remote_channel(
                settings,
                row["channel_id"],
                row["resource_id"],
                transport=transport,
                access_token_provider=access_token_provider,
            )
        except DriveWatchError:
            store.record_drive_cleanup_result(
                row["channel_id"], success=False, error_type="DriveWatchError"
            )
        else:
            succeeded += 1
            store.record_drive_cleanup_result(row["channel_id"], success=True)
    return {"attempted": attempted, "succeeded": succeeded}


def launch_debounced_runner(store: PmtStore, settings: TransferSettings) -> None:
    key = str(store.path)
    existing = _ACTIVE_TASKS.get(key)
    if existing is not None and not existing.done():
        return
    task = asyncio.create_task(_debounced_runner(store, settings))
    _ACTIVE_TASKS[key] = task

    def discard(finished: asyncio.Task[Any]) -> None:
        if not finished.cancelled():
            finished.exception()
        if _ACTIVE_TASKS.get(key) is finished:
            _ACTIVE_TASKS.pop(key, None)

    task.add_done_callback(discard)


def _single_header(request: Request, name: bytes, *, required: bool = True) -> str:
    values = [value for key, value in request.scope.get("headers", []) if key.lower() == name]
    if len(values) != 1:
        if required or values:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail="Invalid Drive notification headers"
            )
        return ""
    try:
        return values[0].decode("ascii")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="Invalid Drive notification headers"
        ) from exc


async def _require_empty_body(request: Request) -> None:
    content_length = request.headers.get("content-length")
    if content_length not in {None, "0"}:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="Drive notifications must have an empty body"
        )
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail="Drive notifications must have an empty body"
            )


def create_drive_notification_router(settings: TransferSettings) -> APIRouter:
    store = PmtStore(settings.pmt_db_path)
    store.initialize()
    router = APIRouter(tags=["PMT Drive notifications"])

    @router.post(CALLBACK_PATH, status_code=status.HTTP_202_ACCEPTED)
    async def drive_notification(request: Request) -> Response:
        if not settings.pmt_drive_watch_enabled:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Drive watch is disabled")
        await _require_empty_body(request)
        channel_id = _single_header(request, b"x-goog-channel-id")
        supplied_token = _single_header(request, b"x-goog-channel-token")
        resource_id = _single_header(request, b"x-goog-resource-id")
        resource_state = _single_header(request, b"x-goog-resource-state")
        message_raw = _single_header(request, b"x-goog-message-number")
        resource_uri = _single_header(request, b"x-goog-resource-uri", required=False)
        changed = _single_header(request, b"x-goog-changed", required=False)
        if (
            CHANNEL_RE.fullmatch(channel_id) is None
            or TOKEN_RE.fullmatch(supplied_token) is None
            or RESOURCE_RE.fullmatch(resource_id) is None
            or MESSAGE_RE.fullmatch(message_raw) is None
            or resource_state not in {"sync", "update"}
            or (resource_uri and len(resource_uri) > 2048)
            or (changed and CHANGED_RE.fullmatch(changed) is None)
        ):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail="Invalid Drive notification headers"
            )
        message_number = int(message_raw)
        if message_number > 9_223_372_036_854_775_807:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail="Invalid Drive notification headers"
            )
        # X-Goog-Changed is optional for file update notifications. When Google omits it,
        # the resource state is still authoritative, so enqueue one bounded reconciliation.
        channel = store.get_drive_channel(channel_id)
        if channel is None or channel["file_id"] != settings.pmt_drive_spreadsheet_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Drive channel is not authorized")
        expected_token = derive_channel_token(
            settings.pmt_drive_webhook_secret, channel_id, settings.pmt_drive_spreadsheet_id
        )
        if not hmac.compare_digest(expected_token, supplied_token):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Drive channel is not authorized")
        if (
            resource_uri
            and channel["resource_uri"]
            and not hmac.compare_digest(resource_uri, channel["resource_uri"])
        ):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, detail="Drive resource is not authorized"
            )
        try:
            outcome = store.record_drive_notification(
                channel_id,
                token_hash(supplied_token),
                resource_id,
                message_number,
                resource_state,
            )
        except PermissionError as exc:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, detail="Drive notification is not authorized"
            ) from exc
        if outcome == "pending":
            launch_debounced_runner(store, settings)
            return Response(status_code=status.HTTP_202_ACCEPTED)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
