from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

DEFAULT_HOME_ALLOWLIST_PREFIX: Final = Path("/home/fhnasgf").resolve()
DEFAULT_BASE_DIR_NAME: Final = "mcp-transfer"


@dataclass(frozen=True, slots=True)
class TransferSettings:
    server_name: str
    base_dir: Path
    max_file_mb: int
    public_url: str
    web_admin_password: str
    session_secret: str
    web_admin_username: str = "admin"
    google_docs_service_account_file: Path | None = None
    google_docs_timeout_seconds: float = 30.0
    pmt_drive_watch_enabled: bool = False
    pmt_drive_spreadsheet_id: str = ""
    pmt_drive_csv_url: str = ""
    pmt_drive_webhook_secret: str = ""
    pmt_drive_webhook_callback_url: str = ""
    pmt_drive_assignee: str = "Farhan"
    pmt_drive_dev_status: str = "To-Do"
    pmt_drive_project: str = "HMX"
    pmt_drive_target_branch: str = "Human-Resources"

    @property
    def inbox_dir(self) -> Path:
        return self.base_dir / "inbox"

    @property
    def metadata_dir(self) -> Path:
        return self.base_dir / "metadata"

    @property
    def config_dir(self) -> Path:
        return self.base_dir / "config"

    @property
    def logs_dir(self) -> Path:
        return self.base_dir / "logs"

    @property
    def pmt_dir(self) -> Path:
        return self.base_dir / "pmt"

    @property
    def pmt_db_path(self) -> Path:
        return self.pmt_dir / "pmt.sqlite3"


@dataclass(frozen=True, slots=True)
class Destination:
    name: str
    url: str
    token_env: str


@dataclass(frozen=True, slots=True)
class AllowedPeer:
    name: str
    token_hash: str
    enabled: bool
    scopes: tuple[str, ...] = ()


def _env_value(env: Mapping[str, str], key: str, default: str | None = None) -> str:
    value = env.get(key, default)
    if value is None or value == "":
        raise ValueError(f"missing required environment variable: {key}")
    return value


def _load_allowlist_prefix(env: Mapping[str, str]) -> Path:
    raw = env.get("MCP_TRANSFER_HOME_ALLOWLIST_PREFIX")
    if raw is None:
        return DEFAULT_HOME_ALLOWLIST_PREFIX

    raw = raw.strip()
    if not raw or not Path(raw).is_absolute():
        raise ValueError("MCP_TRANSFER_HOME_ALLOWLIST_PREFIX must be a non-empty absolute path")
    return Path(raw).expanduser().resolve()


def _ensure_under_home(path: Path, allowlist_prefix: Path) -> Path:
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(allowlist_prefix)
    except ValueError as exc:
        raise ValueError(f"base dir must be under {allowlist_prefix}") from exc
    return resolved


def _load_base_dir(env: Mapping[str, str]) -> Path:
    allowlist_prefix = _load_allowlist_prefix(env)
    raw = env.get("MCP_TRANSFER_BASE_DIR")
    if raw is None:
        return allowlist_prefix / DEFAULT_BASE_DIR_NAME

    raw = raw.strip()
    if not raw or not Path(raw).is_absolute():
        raise ValueError(
            f"MCP_TRANSFER_BASE_DIR must be a non-empty absolute path under {allowlist_prefix}",
        )
    return _ensure_under_home(Path(raw), allowlist_prefix)


def _validate_public_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("MCP_TRANSFER_PUBLIC_URL is invalid") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("MCP_TRANSFER_PUBLIC_URL must be an exact HTTPS origin without a port")
    return value.rstrip("/")


def _validate_drive_callback(value: str, public_url: str) -> str:
    parsed = urlsplit(value)
    public = urlsplit(public_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != public.hostname
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != "/api/v1/pmt/drive-notifications/bug-tracker"
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Drive webhook callback must be the exact HTTPS same-origin callback URL")
    return value


def load_settings(env: Mapping[str, str] | None = None) -> TransferSettings:
    source = os.environ if env is None else env
    max_file_mb = int(source.get("MCP_TRANSFER_MAX_FILE_MB", "50"))
    if max_file_mb < 1 or max_file_mb > 50:
        raise ValueError("max file size must be between 1 and 50 MB")

    docs_credential_raw = source.get("MCP_PMT_GOOGLE_DOCS_SERVICE_ACCOUNT_FILE", "").strip()
    docs_credential: Path | None = None
    if docs_credential_raw:
        candidate = Path(docs_credential_raw).expanduser()
        if not candidate.is_absolute():
            raise ValueError("MCP_PMT_GOOGLE_DOCS_SERVICE_ACCOUNT_FILE must be an absolute path")
        docs_credential = candidate.resolve()
    try:
        docs_timeout = float(source.get("MCP_PMT_GOOGLE_DOCS_TIMEOUT_SECONDS", "30"))
    except ValueError as exc:
        raise ValueError("MCP_PMT_GOOGLE_DOCS_TIMEOUT_SECONDS must be a number") from exc
    if not 3 <= docs_timeout <= 60:
        raise ValueError("MCP_PMT_GOOGLE_DOCS_TIMEOUT_SECONDS must be between 3 and 60")

    public_url_raw = _env_value(source, "MCP_TRANSFER_PUBLIC_URL").rstrip("/")
    enabled_raw = source.get("MCP_PMT_DRIVE_WATCH_ENABLED", "false").strip().lower()
    if enabled_raw not in {"true", "false"}:
        raise ValueError("MCP_PMT_DRIVE_WATCH_ENABLED must be true or false")
    drive_watch_enabled = enabled_raw == "true"
    # Legacy standalone deployments may use HTTP or an explicit local port. The strict
    # public HTTPS origin contract is required only when it becomes a Drive callback origin.
    public_url = _validate_public_url(public_url_raw) if drive_watch_enabled else public_url_raw
    spreadsheet_id = source.get("MCP_PMT_DRIVE_SPREADSHEET_ID", "").strip()
    csv_url = source.get("MCP_PMT_DRIVE_CSV_URL", "").strip()
    webhook_secret = source.get("MCP_PMT_DRIVE_WEBHOOK_SECRET", "")
    callback_url = source.get("MCP_PMT_DRIVE_WEBHOOK_CALLBACK_URL", "").strip()
    callback_url = callback_url or (f"{public_url}/api/v1/pmt/drive-notifications/bug-tracker")
    if drive_watch_enabled:
        if re.fullmatch(r"[A-Za-z0-9_-]{1,200}", spreadsheet_id) is None:
            raise ValueError("MCP_PMT_DRIVE_SPREADSHEET_ID is invalid")
        from mcp_transfer_node.pmt_sheet import sheet_source_id

        if sheet_source_id(csv_url).split(":", 1)[0] != spreadsheet_id:
            raise ValueError("MCP_PMT_DRIVE_CSV_URL must match the configured spreadsheet ID")
        if docs_credential is None:
            raise ValueError("Drive watch requires MCP_PMT_GOOGLE_DOCS_SERVICE_ACCOUNT_FILE")
        if len(webhook_secret.encode()) < 32:
            raise ValueError("MCP_PMT_DRIVE_WEBHOOK_SECRET must be at least 32 bytes")
        _validate_drive_callback(callback_url, public_url)

    return TransferSettings(
        server_name=_env_value(source, "MCP_TRANSFER_SERVER_NAME"),
        base_dir=_load_base_dir(source),
        max_file_mb=max_file_mb,
        public_url=public_url,
        web_admin_password=_env_value(source, "MCP_TRANSFER_WEB_ADMIN_PASSWORD"),
        session_secret=_env_value(source, "MCP_TRANSFER_SESSION_SECRET"),
        web_admin_username=source.get("MCP_TRANSFER_WEB_ADMIN_USERNAME", "admin").strip()
        or "admin",
        google_docs_service_account_file=docs_credential,
        google_docs_timeout_seconds=docs_timeout,
        pmt_drive_watch_enabled=drive_watch_enabled,
        pmt_drive_spreadsheet_id=spreadsheet_id,
        pmt_drive_csv_url=csv_url,
        pmt_drive_webhook_secret=webhook_secret,
        pmt_drive_webhook_callback_url=callback_url,
        pmt_drive_assignee=source.get("MCP_PMT_DRIVE_ASSIGNEE", "Farhan").strip() or "Farhan",
        pmt_drive_dev_status=source.get("MCP_PMT_DRIVE_DEV_STATUS", "To-Do").strip() or "To-Do",
        pmt_drive_project=source.get("MCP_PMT_DRIVE_PROJECT", "HMX").strip() or "HMX",
        pmt_drive_target_branch=source.get("MCP_PMT_DRIVE_TARGET_BRANCH", "Human-Resources").strip()
        or "Human-Resources",
    )


def _json_object(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: invalid JSON") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected a top-level JSON object")
    return payload


def _items(
    path: Path,
    payload: dict[str, object],
    key: str,
    required: tuple[str, ...],
) -> list[dict[str, object]]:
    items = payload.get(key)
    if not isinstance(items, list):
        raise ValueError(f"{path}: expected '{key}' to be a JSON array")

    parsed: list[dict[str, object]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"{path}: {key}[{index}] must be a JSON object")
        missing = [required_key for required_key in required if required_key not in item]
        if missing:
            raise ValueError(
                f"{path}: {key}[{index}] missing required keys: {', '.join(missing)}",
            )
        parsed.append(item)
    return parsed


def load_destinations(config_path: Path) -> list[Destination]:
    if not config_path.exists():
        return []

    return [
        Destination(
            name=str(item["name"]),
            url=str(item["url"]).rstrip("/"),
            token_env=str(item["tokenEnv"]),
        )
        for item in _items(
            config_path,
            _json_object(config_path),
            "destinations",
            ("name", "url", "tokenEnv"),
        )
    ]


def load_allowed_peers(config_path: Path) -> list[AllowedPeer]:
    if not config_path.exists():
        return []

    return [
        AllowedPeer(
            name=str(item["name"]),
            token_hash=str(item["tokenHash"]),
            enabled=bool(item["enabled"]),
            scopes=tuple(
                str(scope).strip() for scope in item.get("scopes", []) if str(scope).strip()
            )
            if isinstance(item.get("scopes", []), list)
            else (),
        )
        for item in _items(
            config_path,
            _json_object(config_path),
            "allowedPeers",
            ("name", "tokenHash", "enabled"),
        )
    ]
