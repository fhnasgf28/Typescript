from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

HOME_ALLOWLIST_PREFIX = Path("/home/fhnasgf").resolve()
DEFAULT_BASE_DIR = HOME_ALLOWLIST_PREFIX / "mcp-transfer"


@dataclass(frozen=True)
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


@dataclass(frozen=True)
class Destination:
    name: str
    url: str
    token_env: str


@dataclass(frozen=True)
class AllowedPeer:
    name: str
    token_hash: str
    enabled: bool


def _env_value(env: Mapping[str, str], key: str, default: str | None = None) -> str:
    value = env.get(key, default)
    if value is None or value == "":
        raise ValueError(f"missing required environment variable: {key}")
    return value


def _ensure_under_home(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(HOME_ALLOWLIST_PREFIX)
    except ValueError as exc:
        raise ValueError("base dir must be under /home/fhnasgf") from exc
    return resolved


def load_settings(env: Mapping[str, str] | None = None) -> TransferSettings:
    source = os.environ if env is None else env
    server_name = _env_value(source, "MCP_TRANSFER_SERVER_NAME")
    base_dir = _ensure_under_home(Path(source.get("MCP_TRANSFER_BASE_DIR", str(DEFAULT_BASE_DIR))))
    max_file_mb = int(source.get("MCP_TRANSFER_MAX_FILE_MB", "50"))
    if max_file_mb < 1 or max_file_mb > 50:
        raise ValueError("max file size must be between 1 and 50 MB")
    return TransferSettings(
        server_name=server_name,
        base_dir=base_dir,
        max_file_mb=max_file_mb,
        public_url=_env_value(source, "MCP_TRANSFER_PUBLIC_URL"),
        web_admin_password=_env_value(source, "MCP_TRANSFER_WEB_ADMIN_PASSWORD"),
        session_secret=_env_value(source, "MCP_TRANSFER_SESSION_SECRET"),
    )


def load_destinations(config_path: Path) -> list[Destination]:
    if not config_path.exists():
        return []
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    return [
        Destination(
            name=str(item["name"]),
            url=str(item["url"]).rstrip("/"),
            token_env=str(item["tokenEnv"]),
        )
        for item in payload.get("destinations", [])
    ]


def load_allowed_peers(config_path: Path) -> list[AllowedPeer]:
    if not config_path.exists():
        return []
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    return [
        AllowedPeer(
            name=str(item["name"]),
            token_hash=str(item["tokenHash"]),
            enabled=bool(item["enabled"]),
        )
        for item in payload.get("allowedPeers", [])
    ]
