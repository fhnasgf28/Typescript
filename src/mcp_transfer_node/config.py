from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

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


def load_settings(env: Mapping[str, str] | None = None) -> TransferSettings:
    source = os.environ if env is None else env
    max_file_mb = int(source.get("MCP_TRANSFER_MAX_FILE_MB", "50"))
    if max_file_mb < 1 or max_file_mb > 50:
        raise ValueError("max file size must be between 1 and 50 MB")

    return TransferSettings(
        server_name=_env_value(source, "MCP_TRANSFER_SERVER_NAME"),
        base_dir=_load_base_dir(source),
        max_file_mb=max_file_mb,
        public_url=_env_value(source, "MCP_TRANSFER_PUBLIC_URL"),
        web_admin_password=_env_value(source, "MCP_TRANSFER_WEB_ADMIN_PASSWORD"),
        session_secret=_env_value(source, "MCP_TRANSFER_SESSION_SECRET"),
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
        )
        for item in _items(
            config_path,
            _json_object(config_path),
            "allowedPeers",
            ("name", "tokenHash", "enabled"),
        )
    ]
