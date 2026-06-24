# MCP File Transfer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an MVP `mcp-transfer-node` service that lets AI agents and humans send small files directly between public servers through Cloudflare Tunnel, with per-peer API tokens and a simple web UI.

**Architecture:** Create a Python FastAPI application for the local HTTP receiver and simple web UI, plus a stdio MCP server command for AI agents. Each server stores received files under `/home/fhnasgf/mcp-transfer/inbox/`, records JSONL metadata, and is exposed publicly by a per-server Cloudflare Tunnel subdomain such as `server-a.clipperyt.online`.

**Tech Stack:** Python 3.11+, FastAPI, Uvicorn, python-multipart, Jinja2, Starlette sessions, official Python MCP SDK, pytest, FastAPI TestClient/httpx.

## Global Constraints

- Transfer model: direct server-to-server.
- Network model: all participating servers are reachable through public hostnames.
- Exposure model: each server exposes its local receiver through Cloudflare Tunnel.
- Public addressing: one subdomain per server under `clipperyt.online`, for example `server-a.clipperyt.online`.
- Security model: API token per server/peer.
- Manual access: simple web upload interface.
- Initial file size target: files under 50 MB.
- File type policy: all file types are allowed and stored as binary data, as long as they are regular files and within the size limit.
- Destination storage policy: received files are stored only under an allowlisted folder below `/home/fhnasgf/`.
- Initial data root: `/home/fhnasgf/mcp-transfer/`.
- Do not log raw tokens.
- Do not commit `.env` or real secrets.
- Do not write uploaded files outside `/home/fhnasgf/mcp-transfer/inbox/`.

---

## File Structure

Create the implementation as a focused Python project at:

```text
/home/fhnasgf/mcp-transfer-node/
```

Project files:

```text
/home/fhnasgf/mcp-transfer-node/
├── .env.example
├── .gitignore
├── README.md
├── pyproject.toml
├── src/
│   └── mcp_transfer_node/
│       ├── __init__.py
│       ├── api.py
│       ├── app.py
│       ├── auth.py
│       ├── config.py
│       ├── files.py
│       ├── logging_config.py
│       ├── mcp_server.py
│       ├── metadata.py
│       ├── responses.py
│       ├── web.py
│       └── templates/
│           ├── index.html
│           └── login.html
└── tests/
    ├── conftest.py
    ├── test_api.py
    ├── test_auth.py
    ├── test_config.py
    ├── test_files.py
    ├── test_mcp_server.py
    ├── test_metadata.py
    └── test_web.py
```

Runtime data files are outside the repo:

```text
/home/fhnasgf/mcp-transfer/
├── inbox/
├── metadata/
│   └── transfers.jsonl
├── config/
│   ├── peers.json
│   └── destinations.json
└── logs/
    └── app.log
```

Responsibilities:

- `config.py` loads environment variables and JSON config files.
- `auth.py` hashes and verifies peer tokens and web admin password checks.
- `files.py` sanitizes filenames, enforces size limits, hashes bytes, and performs safe inbox writes.
- `metadata.py` owns append/read/update behavior for `transfers.jsonl`.
- `responses.py` defines the consistent API response envelope.
- `api.py` defines `/health` and `/api/*` endpoints.
- `web.py` defines login, logout, upload, inbox, download, and delete browser routes.
- `app.py` assembles the FastAPI application.
- `mcp_server.py` exposes stdio MCP tools for agent use.
- `logging_config.py` configures file logging without raw tokens.

---

### Task 1: Project Scaffold and Configuration Loader

**Files:**
- Create: `/home/fhnasgf/mcp-transfer-node/pyproject.toml`
- Create: `/home/fhnasgf/mcp-transfer-node/.gitignore`
- Create: `/home/fhnasgf/mcp-transfer-node/.env.example`
- Create: `/home/fhnasgf/mcp-transfer-node/README.md`
- Create: `/home/fhnasgf/mcp-transfer-node/src/mcp_transfer_node/__init__.py`
- Create: `/home/fhnasgf/mcp-transfer-node/src/mcp_transfer_node/config.py`
- Test: `/home/fhnasgf/mcp-transfer-node/tests/test_config.py`

**Interfaces:**
- Produces: `TransferSettings` dataclass with fields `server_name: str`, `base_dir: Path`, `max_file_mb: int`, `public_url: str`, `web_admin_password: str`, `session_secret: str`.
- Produces: `Destination` dataclass with fields `name: str`, `url: str`, `token_env: str`.
- Produces: `AllowedPeer` dataclass with fields `name: str`, `token_hash: str`, `enabled: bool`.
- Produces: `load_settings(env: Mapping[str, str] | None = None) -> TransferSettings`.
- Produces: `load_destinations(config_path: Path) -> list[Destination]`.
- Produces: `load_allowed_peers(config_path: Path) -> list[AllowedPeer]`.
- Later tasks consume these exact names.

- [ ] **Step 1: Create the project scaffold files**

Create `pyproject.toml` with:

```toml
[build-system]
requires = ["hatchling>=1.25.0"]
build-backend = "hatchling.build"

[project]
name = "mcp-transfer-node"
version = "0.1.0"
description = "Direct server-to-server file transfer node with Web UI and MCP tools"
requires-python = ">=3.11"
dependencies = [
  "fastapi>=0.115.0",
  "uvicorn[standard]>=0.30.0",
  "python-multipart>=0.0.9",
  "jinja2>=3.1.4",
  "itsdangerous>=2.2.0",
  "httpx>=0.27.0",
  "mcp>=1.2.0"
]

[project.optional-dependencies]
test = [
  "pytest>=8.2.0",
  "pytest-asyncio>=0.23.0",
  "ruff>=0.6.0"
]

[project.scripts]
mcp-transfer-serve = "mcp_transfer_node.app:run"
mcp-transfer-mcp = "mcp_transfer_node.mcp_server:run"

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]

[tool.ruff]
line-length = 100
src = ["src", "tests"]
```

Create `.gitignore` with:

```gitignore
.venv/
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
.env
.env.*
!.env.example
```

Create `.env.example` with development-safe example values:

```env
MCP_TRANSFER_SERVER_NAME=server-a
MCP_TRANSFER_BASE_DIR=/home/fhnasgf/mcp-transfer
MCP_TRANSFER_MAX_FILE_MB=50
MCP_TRANSFER_WEB_ADMIN_PASSWORD=dev-admin-password-change-before-use
MCP_TRANSFER_SESSION_SECRET=dev-session-secret-32-bytes-minimum-123456
MCP_TRANSFER_PUBLIC_URL=https://server-a.clipperyt.online
MCP_TRANSFER_DEST_SERVER_B_TOKEN=dev-peer-token-change-before-use
```

Create `src/mcp_transfer_node/__init__.py` with:

```python
"""MCP Transfer Node."""

__all__ = ["__version__"]
__version__ = "0.1.0"
```

- [ ] **Step 2: Write failing config tests**

Create `tests/test_config.py` with:

```python
from pathlib import Path

import pytest

from mcp_transfer_node.config import (
    load_allowed_peers,
    load_destinations,
    load_settings,
)


def test_load_settings_uses_environment_values(tmp_path: Path) -> None:
    env = {
        "MCP_TRANSFER_SERVER_NAME": "server-b",
        "MCP_TRANSFER_BASE_DIR": str(tmp_path),
        "MCP_TRANSFER_MAX_FILE_MB": "50",
        "MCP_TRANSFER_WEB_ADMIN_PASSWORD": "admin-password",
        "MCP_TRANSFER_SESSION_SECRET": "session-secret-with-more-than-32-chars",
        "MCP_TRANSFER_PUBLIC_URL": "https://server-b.clipperyt.online",
    }

    settings = load_settings(env)

    assert settings.server_name == "server-b"
    assert settings.base_dir == tmp_path
    assert settings.max_file_mb == 50
    assert settings.web_admin_password == "admin-password"
    assert settings.public_url == "https://server-b.clipperyt.online"


def test_load_settings_rejects_base_dir_outside_home() -> None:
    env = {
        "MCP_TRANSFER_SERVER_NAME": "server-b",
        "MCP_TRANSFER_BASE_DIR": "/tmp/mcp-transfer",
        "MCP_TRANSFER_MAX_FILE_MB": "50",
        "MCP_TRANSFER_WEB_ADMIN_PASSWORD": "admin-password",
        "MCP_TRANSFER_SESSION_SECRET": "session-secret-with-more-than-32-chars",
        "MCP_TRANSFER_PUBLIC_URL": "https://server-b.clipperyt.online",
    }

    with pytest.raises(ValueError, match="base dir must be under /home/fhnasgf"):
        load_settings(env)


def test_load_destinations_reads_aliases(tmp_path: Path) -> None:
    config_path = tmp_path / "destinations.json"
    config_path.write_text(
        '{"destinations":[{"name":"server-b","url":"https://server-b.clipperyt.online","tokenEnv":"MCP_TRANSFER_DEST_SERVER_B_TOKEN"}]}',
        encoding="utf-8",
    )

    destinations = load_destinations(config_path)

    assert destinations[0].name == "server-b"
    assert destinations[0].url == "https://server-b.clipperyt.online"
    assert destinations[0].token_env == "MCP_TRANSFER_DEST_SERVER_B_TOKEN"


def test_load_allowed_peers_reads_enabled_peers(tmp_path: Path) -> None:
    config_path = tmp_path / "peers.json"
    config_path.write_text(
        '{"allowedPeers":[{"name":"server-a","tokenHash":"sha256:abc","enabled":true}]}',
        encoding="utf-8",
    )

    peers = load_allowed_peers(config_path)

    assert peers[0].name == "server-a"
    assert peers[0].token_hash == "sha256:abc"
    assert peers[0].enabled is True
```

- [ ] **Step 3: Run config tests to verify they fail**

Run:

```bash
cd /home/fhnasgf/mcp-transfer-node
python -m pytest tests/test_config.py -v
```

Expected: FAIL because `mcp_transfer_node.config` does not exist yet.

- [ ] **Step 4: Implement configuration loader**

Create `src/mcp_transfer_node/config.py` with:

```python
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
    base_dir = _ensure_under_home(
        Path(source.get("MCP_TRANSFER_BASE_DIR", str(DEFAULT_BASE_DIR)))
    )
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
```

- [ ] **Step 5: Run config tests to verify they pass**

Run:

```bash
cd /home/fhnasgf/mcp-transfer-node
python -m pytest tests/test_config.py -v
```

Expected: PASS for all tests in `test_config.py`.

- [ ] **Step 6: Commit scaffold and config**

Run:

```bash
cd /home/fhnasgf/mcp-transfer-node
git add pyproject.toml .gitignore .env.example README.md src/mcp_transfer_node/__init__.py src/mcp_transfer_node/config.py tests/test_config.py
git commit -m "feat: scaffold transfer node config"
```

---

### Task 2: Core Auth, Responses, File Safety, and Metadata

**Files:**
- Create: `/home/fhnasgf/mcp-transfer-node/src/mcp_transfer_node/auth.py`
- Create: `/home/fhnasgf/mcp-transfer-node/src/mcp_transfer_node/files.py`
- Create: `/home/fhnasgf/mcp-transfer-node/src/mcp_transfer_node/metadata.py`
- Create: `/home/fhnasgf/mcp-transfer-node/src/mcp_transfer_node/responses.py`
- Test: `/home/fhnasgf/mcp-transfer-node/tests/test_auth.py`
- Test: `/home/fhnasgf/mcp-transfer-node/tests/test_files.py`
- Test: `/home/fhnasgf/mcp-transfer-node/tests/test_metadata.py`

**Interfaces:**
- Consumes: `AllowedPeer`, `TransferSettings` from `config.py`.
- Produces: `hash_token(token: str) -> str`.
- Produces: `verify_token(token: str, token_hash: str) -> bool`.
- Produces: `authenticate_peer(token: str, source: str, peers: Sequence[AllowedPeer]) -> AllowedPeer | None`.
- Produces: `sanitize_filename(filename: str) -> str`.
- Produces: `build_stored_filename(received_at: datetime, source: str, original_filename: str, transfer_id: str) -> str`.
- Produces: `sha256_file(path: Path) -> str`.
- Produces: `ensure_runtime_dirs(settings: TransferSettings) -> None`.
- Produces: `TransferRecord` dataclass.
- Produces: `append_record(metadata_path: Path, record: TransferRecord) -> None`.
- Produces: `list_records(metadata_path: Path, limit: int = 50) -> list[TransferRecord]`.
- Produces: `get_record(metadata_path: Path, transfer_id: str) -> TransferRecord | None`.
- Produces: `mark_deleted(metadata_path: Path, transfer_id: str) -> bool`.
- Produces: `success_response(data: dict) -> dict` and `error_response(code: str, message: str) -> dict`.

- [ ] **Step 1: Write failing core tests**

Create `tests/test_auth.py` with:

```python
from mcp_transfer_node.auth import authenticate_peer, hash_token, verify_token
from mcp_transfer_node.config import AllowedPeer


def test_hash_and_verify_token() -> None:
    token_hash = hash_token("secret-token")

    assert token_hash.startswith("sha256:")
    assert verify_token("secret-token", token_hash) is True
    assert verify_token("wrong-token", token_hash) is False


def test_authenticate_peer_requires_enabled_peer_and_matching_source() -> None:
    peers = [AllowedPeer(name="server-a", token_hash=hash_token("secret-token"), enabled=True)]

    peer = authenticate_peer("secret-token", "server-a", peers)

    assert peer is not None
    assert peer.name == "server-a"
    assert authenticate_peer("secret-token", "server-b", peers) is None
    assert authenticate_peer("wrong-token", "server-a", peers) is None
```

Create `tests/test_files.py` with:

```python
from datetime import datetime, timezone
from pathlib import Path

from mcp_transfer_node.config import TransferSettings
from mcp_transfer_node.files import (
    build_stored_filename,
    ensure_runtime_dirs,
    sanitize_filename,
    sha256_file,
)


def test_sanitize_filename_strips_paths_and_keeps_extension() -> None:
    assert sanitize_filename("../../.ssh/id_rsa") == "id_rsa"
    assert sanitize_filename("/etc/passwd") == "passwd"
    assert sanitize_filename("my report!!.pdf") == "my-report.pdf"
    assert sanitize_filename("archive.tar.gz") == "archive.tar.gz"


def test_build_stored_filename_is_timestamp_source_and_safe_name() -> None:
    received_at = datetime(2026, 6, 24, 21, 5, 1, tzinfo=timezone.utc)

    stored = build_stored_filename(received_at, "server-a", "my report!!.pdf", "transfer_abc")

    assert stored == "2026-06-24T210501Z-server-a-my-report.pdf"


def test_sha256_file_hashes_binary_bytes(tmp_path: Path) -> None:
    file_path = tmp_path / "image.bin"
    file_path.write_bytes(b"\x00\x01\x02")

    assert sha256_file(file_path) == "ae4b3280e56e2faf83f414a6e3dabe9d5fbe18976544c05fed121acedf8ccabd"


def test_ensure_runtime_dirs_creates_expected_tree(tmp_path: Path) -> None:
    settings = TransferSettings(
        server_name="server-a",
        base_dir=tmp_path,
        max_file_mb=50,
        public_url="https://server-a.clipperyt.online",
        web_admin_password="admin-password",
        session_secret="session-secret-with-more-than-32-chars",
    )

    ensure_runtime_dirs(settings)

    assert settings.inbox_dir.is_dir()
    assert settings.metadata_dir.is_dir()
    assert settings.config_dir.is_dir()
    assert settings.logs_dir.is_dir()
```

Create `tests/test_metadata.py` with:

```python
from datetime import datetime, timezone
from pathlib import Path

from mcp_transfer_node.metadata import (
    TransferRecord,
    append_record,
    get_record,
    list_records,
    mark_deleted,
)


def make_record(transfer_id: str = "transfer_abc") -> TransferRecord:
    return TransferRecord(
        id=transfer_id,
        received_at=datetime(2026, 6, 24, 21, 5, 1, tzinfo=timezone.utc),
        source="server-a",
        original_filename="report.txt",
        stored_filename="2026-06-24T210501Z-server-a-report.txt",
        stored_path="/home/fhnasgf/mcp-transfer/inbox/2026-06-24T210501Z-server-a-report.txt",
        size_bytes=12,
        sha256="abc123",
        note="report terbaru",
        status="received",
    )


def test_append_and_list_records(tmp_path: Path) -> None:
    metadata_path = tmp_path / "transfers.jsonl"
    append_record(metadata_path, make_record())

    records = list_records(metadata_path)

    assert len(records) == 1
    assert records[0].id == "transfer_abc"
    assert records[0].source == "server-a"


def test_get_record_returns_matching_transfer(tmp_path: Path) -> None:
    metadata_path = tmp_path / "transfers.jsonl"
    append_record(metadata_path, make_record("transfer_1"))
    append_record(metadata_path, make_record("transfer_2"))

    record = get_record(metadata_path, "transfer_2")

    assert record is not None
    assert record.id == "transfer_2"


def test_mark_deleted_updates_record_status(tmp_path: Path) -> None:
    metadata_path = tmp_path / "transfers.jsonl"
    append_record(metadata_path, make_record())

    changed = mark_deleted(metadata_path, "transfer_abc")

    assert changed is True
    assert get_record(metadata_path, "transfer_abc").status == "deleted"
```

- [ ] **Step 2: Run core tests to verify they fail**

Run:

```bash
cd /home/fhnasgf/mcp-transfer-node
python -m pytest tests/test_auth.py tests/test_files.py tests/test_metadata.py -v
```

Expected: FAIL because modules are not implemented yet.

- [ ] **Step 3: Implement auth helpers**

Create `src/mcp_transfer_node/auth.py` with:

```python
from __future__ import annotations

import hashlib
import hmac
from collections.abc import Sequence

from mcp_transfer_node.config import AllowedPeer


def hash_token(token: str) -> str:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def verify_token(token: str, token_hash: str) -> bool:
    if not token_hash.startswith("sha256:"):
        return False
    return hmac.compare_digest(hash_token(token), token_hash)


def authenticate_peer(
    token: str,
    source: str,
    peers: Sequence[AllowedPeer],
) -> AllowedPeer | None:
    for peer in peers:
        if not peer.enabled:
            continue
        if peer.name != source:
            continue
        if verify_token(token, peer.token_hash):
            return peer
    return None


def verify_web_password(candidate: str, expected: str) -> bool:
    return hmac.compare_digest(candidate, expected)
```

- [ ] **Step 4: Implement response envelope**

Create `src/mcp_transfer_node/responses.py` with:

```python
from __future__ import annotations

from typing import Any


def success_response(data: dict[str, Any]) -> dict[str, Any]:
    return {"success": True, "data": data, "error": None}


def error_response(code: str, message: str) -> dict[str, Any]:
    return {"success": False, "data": None, "error": {"code": code, "message": message}}
```

- [ ] **Step 5: Implement file helpers**

Create `src/mcp_transfer_node/files.py` with:

```python
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from mcp_transfer_node.config import TransferSettings

UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
MULTIPLE_DASHES = re.compile(r"-+")


def ensure_runtime_dirs(settings: TransferSettings) -> None:
    settings.inbox_dir.mkdir(parents=True, exist_ok=True)
    settings.metadata_dir.mkdir(parents=True, exist_ok=True)
    settings.config_dir.mkdir(parents=True, exist_ok=True)
    settings.logs_dir.mkdir(parents=True, exist_ok=True)


def sanitize_filename(filename: str) -> str:
    name = Path(filename).name
    if name in {"", ".", ".."}:
        name = "uploaded-file"
    sanitized = UNSAFE_FILENAME_CHARS.sub("-", name).strip(".-")
    sanitized = MULTIPLE_DASHES.sub("-", sanitized)
    return sanitized or "uploaded-file"


def sanitize_source(source: str) -> str:
    sanitized = UNSAFE_FILENAME_CHARS.sub("-", source).strip(".-")
    sanitized = MULTIPLE_DASHES.sub("-", sanitized)
    return sanitized or "unknown"


def build_stored_filename(
    received_at: datetime,
    source: str,
    original_filename: str,
    transfer_id: str,
) -> str:
    timestamp = received_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    source_name = sanitize_source(source)
    safe_name = sanitize_filename(original_filename)
    candidate = f"{timestamp}-{source_name}-{safe_name}"
    if len(candidate) <= 180:
        return candidate
    suffix = sanitize_source(transfer_id)
    extension = "".join(Path(safe_name).suffixes)[-20:]
    stem = Path(safe_name).stem[:80]
    return f"{timestamp}-{source_name}-{stem}-{suffix}{extension}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
```

- [ ] **Step 6: Implement metadata store**

Create `src/mcp_transfer_node/metadata.py` with:

```python
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class TransferRecord:
    id: str
    received_at: datetime
    source: str
    original_filename: str
    stored_filename: str
    stored_path: str
    size_bytes: int
    sha256: str
    note: str
    status: str

    def to_json_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["received_at"] = self.received_at.isoformat()
        return payload

    @classmethod
    def from_json_dict(cls, payload: dict[str, object]) -> "TransferRecord":
        return cls(
            id=str(payload["id"]),
            received_at=datetime.fromisoformat(str(payload["received_at"])),
            source=str(payload["source"]),
            original_filename=str(payload["original_filename"]),
            stored_filename=str(payload["stored_filename"]),
            stored_path=str(payload["stored_path"]),
            size_bytes=int(payload["size_bytes"]),
            sha256=str(payload["sha256"]),
            note=str(payload.get("note", "")),
            status=str(payload["status"]),
        )


def append_record(metadata_path: Path, record: TransferRecord) -> None:
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    with metadata_path.open("a", encoding="utf-8") as file_obj:
        file_obj.write(json.dumps(record.to_json_dict(), sort_keys=True) + "\n")


def list_records(metadata_path: Path, limit: int = 50) -> list[TransferRecord]:
    if not metadata_path.exists():
        return []
    records = [
        TransferRecord.from_json_dict(json.loads(line))
        for line in metadata_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return list(reversed(records))[:limit]


def get_record(metadata_path: Path, transfer_id: str) -> TransferRecord | None:
    for record in list_records(metadata_path, limit=10_000):
        if record.id == transfer_id:
            return record
    return None


def mark_deleted(metadata_path: Path, transfer_id: str) -> bool:
    if not metadata_path.exists():
        return False
    records = list(reversed(list_records(metadata_path, limit=10_000)))
    changed = False
    updated: list[TransferRecord] = []
    for record in records:
        if record.id == transfer_id and record.status != "deleted":
            updated.append(
                TransferRecord(
                    id=record.id,
                    received_at=record.received_at,
                    source=record.source,
                    original_filename=record.original_filename,
                    stored_filename=record.stored_filename,
                    stored_path=record.stored_path,
                    size_bytes=record.size_bytes,
                    sha256=record.sha256,
                    note=record.note,
                    status="deleted",
                )
            )
            changed = True
        else:
            updated.append(record)
    if changed:
        metadata_path.write_text(
            "".join(json.dumps(record.to_json_dict(), sort_keys=True) + "\n" for record in updated),
            encoding="utf-8",
        )
    return changed
```

- [ ] **Step 7: Run core tests to verify they pass**

Run:

```bash
cd /home/fhnasgf/mcp-transfer-node
python -m pytest tests/test_auth.py tests/test_files.py tests/test_metadata.py -v
```

Expected: PASS for all tests in these files.

- [ ] **Step 8: Commit core helpers**

Run:

```bash
cd /home/fhnasgf/mcp-transfer-node
git add src/mcp_transfer_node/auth.py src/mcp_transfer_node/files.py src/mcp_transfer_node/metadata.py src/mcp_transfer_node/responses.py tests/test_auth.py tests/test_files.py tests/test_metadata.py
git commit -m "feat: add secure transfer core helpers"
```

---

### Task 3: HTTP API Receiver

**Files:**
- Create: `/home/fhnasgf/mcp-transfer-node/src/mcp_transfer_node/api.py`
- Create: `/home/fhnasgf/mcp-transfer-node/src/mcp_transfer_node/logging_config.py`
- Create: `/home/fhnasgf/mcp-transfer-node/src/mcp_transfer_node/app.py`
- Test: `/home/fhnasgf/mcp-transfer-node/tests/conftest.py`
- Test: `/home/fhnasgf/mcp-transfer-node/tests/test_api.py`

**Interfaces:**
- Consumes: `TransferSettings`, `load_allowed_peers`, `authenticate_peer`, `build_stored_filename`, `sha256_file`, `TransferRecord`, metadata helpers, response helpers.
- Produces: `create_api_router(settings: TransferSettings) -> APIRouter`.
- Produces: `create_app(settings: TransferSettings | None = None) -> FastAPI`.
- Produces: HTTP endpoints `GET /health`, `POST /api/upload`, `GET /api/files`, `GET /api/files/{transfer_id}/download`, `DELETE /api/files/{transfer_id}`.

- [ ] **Step 1: Write failing API tests**

Create `tests/conftest.py` with:

```python
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mcp_transfer_node.auth import hash_token
from mcp_transfer_node.app import create_app
from mcp_transfer_node.config import TransferSettings


@pytest.fixture
def settings(tmp_path: Path) -> TransferSettings:
    return TransferSettings(
        server_name="server-b",
        base_dir=tmp_path / "mcp-transfer",
        max_file_mb=50,
        public_url="https://server-b.clipperyt.online",
        web_admin_password="admin-password",
        session_secret="session-secret-with-more-than-32-chars",
    )


@pytest.fixture
def client(settings: TransferSettings) -> TestClient:
    settings.config_dir.mkdir(parents=True, exist_ok=True)
    (settings.config_dir / "peers.json").write_text(
        '{"allowedPeers":[{"name":"server-a","tokenHash":"'
        + hash_token("valid-token")
        + '","enabled":true}]}',
        encoding="utf-8",
    )
    return TestClient(create_app(settings))
```

Create `tests/test_api.py` with:

```python
from fastapi.testclient import TestClient


def test_health_reports_writable_storage(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["serverName"] == "server-b"
    assert payload["data"]["inboxWritable"] is True


def test_upload_rejects_missing_token(client: TestClient) -> None:
    response = client.post(
        "/api/upload",
        files={"file": ("hello.txt", b"hello", "text/plain")},
        data={"note": "manual test"},
        headers={"X-Transfer-Source": "server-a"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_upload_rejects_wrong_token(client: TestClient) -> None:
    response = client.post(
        "/api/upload",
        files={"file": ("hello.txt", b"hello", "text/plain")},
        headers={"Authorization": "Bearer wrong-token", "X-Transfer-Source": "server-a"},
    )

    assert response.status_code == 401


def test_upload_accepts_binary_file_and_writes_metadata(client: TestClient) -> None:
    response = client.post(
        "/api/upload",
        files={"file": ("image.bin", b"\x00\x01\x02", "application/octet-stream")},
        data={"note": "binary payload"},
        headers={"Authorization": "Bearer valid-token", "X-Transfer-Source": "server-a"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["transferId"].startswith("transfer_")
    assert payload["data"]["storedFilename"].endswith("server-a-image.bin")
    assert payload["data"]["sha256"] == "ae4b3280e56e2faf83f414a6e3dabe9d5fbe18976544c05fed121acedf8ccabd"


def test_list_download_and_delete_uploaded_file(client: TestClient) -> None:
    upload = client.post(
        "/api/upload",
        files={"file": ("hello.txt", b"hello", "text/plain")},
        headers={"Authorization": "Bearer valid-token", "X-Transfer-Source": "server-a"},
    )
    transfer_id = upload.json()["data"]["transferId"]

    listing = client.get(
        "/api/files",
        headers={"Authorization": "Bearer valid-token", "X-Transfer-Source": "server-a"},
    )
    assert listing.status_code == 200
    assert listing.json()["data"]["files"][0]["id"] == transfer_id

    download = client.get(
        f"/api/files/{transfer_id}/download",
        headers={"Authorization": "Bearer valid-token", "X-Transfer-Source": "server-a"},
    )
    assert download.status_code == 200
    assert download.content == b"hello"

    delete = client.delete(
        f"/api/files/{transfer_id}",
        headers={"Authorization": "Bearer valid-token", "X-Transfer-Source": "server-a"},
    )
    assert delete.status_code == 200
    assert delete.json()["data"]["deleted"] is True
```

- [ ] **Step 2: Run API tests to verify they fail**

Run:

```bash
cd /home/fhnasgf/mcp-transfer-node
python -m pytest tests/test_api.py -v
```

Expected: FAIL because `api.py` and `app.py` are not implemented.

- [ ] **Step 3: Implement logging config**

Create `src/mcp_transfer_node/logging_config.py` with:

```python
from __future__ import annotations

import logging
from pathlib import Path


def configure_logging(logs_dir: Path) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.FileHandler(logs_dir / "app.log"), logging.StreamHandler()],
        force=True,
    )
```

- [ ] **Step 4: Implement FastAPI app and API router**

Create `src/mcp_transfer_node/api.py` with:

```python
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse

from mcp_transfer_node.auth import authenticate_peer
from mcp_transfer_node.config import AllowedPeer, TransferSettings, load_allowed_peers
from mcp_transfer_node.files import build_stored_filename, ensure_runtime_dirs, sha256_file
from mcp_transfer_node.metadata import (
    TransferRecord,
    append_record,
    get_record,
    list_records,
    mark_deleted,
)
from mcp_transfer_node.responses import error_response, success_response

logger = logging.getLogger(__name__)


def _metadata_path(settings: TransferSettings) -> Path:
    return settings.metadata_dir / "transfers.jsonl"


def _peers(settings: TransferSettings) -> list[AllowedPeer]:
    return load_allowed_peers(settings.config_dir / "peers.json")


def _record_to_dict(record: TransferRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "receivedAt": record.received_at.isoformat(),
        "source": record.source,
        "originalFilename": record.original_filename,
        "storedFilename": record.stored_filename,
        "storedPath": record.stored_path,
        "sizeBytes": record.size_bytes,
        "sha256": record.sha256,
        "note": record.note,
        "status": record.status,
    }


def create_api_router(settings: TransferSettings) -> APIRouter:
    ensure_runtime_dirs(settings)
    router = APIRouter()

    def require_peer(
        authorization: str | None = Header(default=None),
        x_transfer_source: str | None = Header(default=None),
    ) -> AllowedPeer:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=401,
                detail=error_response("UNAUTHORIZED", "Invalid or missing bearer token"),
            )
        if not x_transfer_source:
            raise HTTPException(
                status_code=401,
                detail=error_response("UNAUTHORIZED", "Missing transfer source"),
            )
        token = authorization.removeprefix("Bearer ").strip()
        peer = authenticate_peer(token, x_transfer_source, _peers(settings))
        if peer is None:
            logger.warning("upload rejected source=%s reason=invalid_credentials", x_transfer_source)
            raise HTTPException(
                status_code=401,
                detail=error_response("UNAUTHORIZED", "Invalid or missing bearer token"),
            )
        return peer

    @router.get("/health")
    def health() -> dict[str, object]:
        ensure_runtime_dirs(settings)
        return success_response(
            {
                "serverName": settings.server_name,
                "status": "ok",
                "inboxWritable": os.access(settings.inbox_dir, os.W_OK),
                "metadataWritable": os.access(settings.metadata_dir, os.W_OK),
                "maxFileMb": settings.max_file_mb,
            }
        )

    @router.post("/api/upload")
    async def upload_file(
        peer: AllowedPeer = Depends(require_peer),
        file: UploadFile = File(...),
        note: str = Form(default=""),
    ) -> dict[str, object]:
        received_at = datetime.now(timezone.utc)
        transfer_id = f"transfer_{uuid.uuid4().hex}"
        stored_filename = build_stored_filename(
            received_at,
            peer.name,
            file.filename or "uploaded-file",
            transfer_id,
        )
        final_path = settings.inbox_dir / stored_filename
        temp_path = settings.inbox_dir / f".{transfer_id}.uploading"
        max_bytes = settings.max_file_mb * 1024 * 1024
        size_bytes = 0
        try:
            with temp_path.open("wb") as output:
                while chunk := await file.read(1024 * 1024):
                    size_bytes += len(chunk)
                    if size_bytes > max_bytes:
                        raise HTTPException(
                            status_code=413,
                            detail=error_response("FILE_TOO_LARGE", "File exceeds 50 MB limit"),
                        )
                    output.write(chunk)
            digest = sha256_file(temp_path)
            temp_path.rename(final_path)
            record = TransferRecord(
                id=transfer_id,
                received_at=received_at,
                source=peer.name,
                original_filename=file.filename or "uploaded-file",
                stored_filename=stored_filename,
                stored_path=str(final_path),
                size_bytes=size_bytes,
                sha256=digest,
                note=note,
                status="received",
            )
            append_record(_metadata_path(settings), record)
            logger.info("upload accepted transfer_id=%s source=%s size=%s", transfer_id, peer.name, size_bytes)
            return success_response(
                {"transferId": transfer_id, "storedFilename": stored_filename, "sha256": digest}
            )
        finally:
            if temp_path.exists():
                temp_path.unlink()

    @router.get("/api/files")
    def files(_: AllowedPeer = Depends(require_peer)) -> dict[str, object]:
        records = [
            _record_to_dict(record)
            for record in list_records(_metadata_path(settings))
            if record.status != "deleted"
        ]
        return success_response({"files": records})

    @router.get("/api/files/{transfer_id}/download")
    def download_file(transfer_id: str, _: AllowedPeer = Depends(require_peer)) -> FileResponse:
        record = get_record(_metadata_path(settings), transfer_id)
        if record is None or record.status == "deleted":
            raise HTTPException(status_code=404, detail=error_response("NOT_FOUND", "Transfer not found"))
        path = Path(record.stored_path)
        if not path.exists():
            raise HTTPException(status_code=404, detail=error_response("NOT_FOUND", "File not found"))
        return FileResponse(path, filename=record.original_filename)

    @router.delete("/api/files/{transfer_id}")
    def delete_file(transfer_id: str, _: AllowedPeer = Depends(require_peer)) -> dict[str, object]:
        record = get_record(_metadata_path(settings), transfer_id)
        if record is None or record.status == "deleted":
            raise HTTPException(status_code=404, detail=error_response("NOT_FOUND", "Transfer not found"))
        path = Path(record.stored_path)
        if path.exists():
            path.unlink()
        changed = mark_deleted(_metadata_path(settings), transfer_id)
        logger.info("file deleted transfer_id=%s", transfer_id)
        return success_response({"deleted": changed})

    return router
```

Create `src/mcp_transfer_node/app.py` with:

```python
from __future__ import annotations

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from mcp_transfer_node.api import create_api_router
from mcp_transfer_node.config import TransferSettings, load_settings
from mcp_transfer_node.files import ensure_runtime_dirs
from mcp_transfer_node.logging_config import configure_logging
from mcp_transfer_node.responses import error_response


def create_app(settings: TransferSettings | None = None) -> FastAPI:
    resolved = settings or load_settings()
    ensure_runtime_dirs(resolved)
    configure_logging(resolved.logs_dir)
    app = FastAPI(title="MCP Transfer Node")
    app.add_middleware(SessionMiddleware, secret_key=resolved.session_secret, max_age=12 * 60 * 60)

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_, exc: HTTPException):
        if isinstance(exc.detail, dict) and "success" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response("HTTP_ERROR", str(exc.detail)),
        )

    app.include_router(create_api_router(resolved))
    return app


def run() -> None:
    uvicorn.run("mcp_transfer_node.app:create_app", factory=True, host="127.0.0.1", port=8787)
```

- [ ] **Step 5: Run API tests to verify they pass**

Run:

```bash
cd /home/fhnasgf/mcp-transfer-node
python -m pytest tests/test_api.py -v
```

Expected: PASS for all API tests.

- [ ] **Step 6: Commit API receiver**

Run:

```bash
cd /home/fhnasgf/mcp-transfer-node
git add src/mcp_transfer_node/api.py src/mcp_transfer_node/app.py src/mcp_transfer_node/logging_config.py tests/conftest.py tests/test_api.py
git commit -m "feat: add authenticated transfer API"
```

---

### Task 4: Simple Web UI

**Files:**
- Create: `/home/fhnasgf/mcp-transfer-node/src/mcp_transfer_node/web.py`
- Create: `/home/fhnasgf/mcp-transfer-node/src/mcp_transfer_node/templates/login.html`
- Create: `/home/fhnasgf/mcp-transfer-node/src/mcp_transfer_node/templates/index.html`
- Modify: `/home/fhnasgf/mcp-transfer-node/src/mcp_transfer_node/app.py`
- Test: `/home/fhnasgf/mcp-transfer-node/tests/test_web.py`

**Interfaces:**
- Consumes: `TransferSettings`, metadata helpers, `verify_web_password`.
- Produces: `create_web_router(settings: TransferSettings) -> APIRouter`.
- Produces: browser routes `GET /`, `GET /login`, `POST /login`, `POST /logout`, `POST /web/upload`, `POST /web/files/{transfer_id}/delete`, `GET /web/files/{transfer_id}/download`.

- [ ] **Step 1: Write failing web tests**

Create `tests/test_web.py` with:

```python
from fastapi.testclient import TestClient


def test_login_page_loads(client: TestClient) -> None:
    response = client.get("/login")

    assert response.status_code == 200
    assert "MCP Transfer Node Login" in response.text


def test_wrong_password_stays_on_login(client: TestClient) -> None:
    response = client.post("/login", data={"password": "wrong"})

    assert response.status_code == 401
    assert "Login gagal" in response.text


def test_correct_password_opens_inbox(client: TestClient) -> None:
    response = client.post("/login", data={"password": "admin-password"}, follow_redirects=True)

    assert response.status_code == 200
    assert "MCP Transfer Node - server-b" in response.text


def test_web_upload_lists_and_deletes_file(client: TestClient) -> None:
    client.post("/login", data={"password": "admin-password"})

    upload = client.post(
        "/web/upload",
        files={"file": ("manual.pdf", b"pdf-bytes", "application/pdf")},
        data={"source": "manual", "note": "from browser"},
        follow_redirects=True,
    )
    assert upload.status_code == 200
    assert "manual.pdf" in upload.text

    transfer_id = upload.text.split("data-transfer-id=\"")[1].split("\"")[0]
    download = client.get(f"/web/files/{transfer_id}/download")
    assert download.status_code == 200
    assert download.content == b"pdf-bytes"

    delete = client.post(f"/web/files/{transfer_id}/delete", follow_redirects=True)
    assert delete.status_code == 200
    assert "manual.pdf" not in delete.text
```

- [ ] **Step 2: Run web tests to verify they fail**

Run:

```bash
cd /home/fhnasgf/mcp-transfer-node
python -m pytest tests/test_web.py -v
```

Expected: FAIL because web routes and templates are missing.

- [ ] **Step 3: Implement web router**

Create `src/mcp_transfer_node/web.py` with:

```python
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from mcp_transfer_node.auth import verify_web_password
from mcp_transfer_node.config import TransferSettings
from mcp_transfer_node.files import build_stored_filename, sha256_file
from mcp_transfer_node.metadata import (
    TransferRecord,
    append_record,
    get_record,
    list_records,
    mark_deleted,
)

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _metadata_path(settings: TransferSettings) -> Path:
    return settings.metadata_dir / "transfers.jsonl"


def _require_login(request: Request) -> None:
    if request.session.get("authenticated") is not True:
        raise HTTPException(status_code=303, headers={"Location": "/login"})


def create_web_router(settings: TransferSettings) -> APIRouter:
    router = APIRouter()

    @router.get("/login", response_class=HTMLResponse)
    def login_page(request: Request) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(
            "login.html",
            {"request": request, "error": ""},
        )

    @router.post("/login", response_class=HTMLResponse)
    def login(request: Request, password: str = Form(...)) -> HTMLResponse | RedirectResponse:
        if not verify_web_password(password, settings.web_admin_password):
            return TEMPLATES.TemplateResponse(
                "login.html",
                {"request": request, "error": "Login gagal"},
                status_code=401,
            )
        request.session["authenticated"] = True
        return RedirectResponse("/", status_code=303)

    @router.post("/logout")
    def logout(request: Request) -> RedirectResponse:
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @router.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        _require_login(request)
        records = [
            record for record in list_records(_metadata_path(settings)) if record.status != "deleted"
        ]
        return TEMPLATES.TemplateResponse(
            "index.html",
            {"request": request, "settings": settings, "records": records, "message": ""},
        )

    @router.post("/web/upload", response_class=HTMLResponse)
    async def web_upload(
        request: Request,
        file: UploadFile = File(...),
        source: str = Form(default="manual"),
        note: str = Form(default=""),
    ) -> RedirectResponse:
        _require_login(request)
        received_at = datetime.now(timezone.utc)
        transfer_id = f"transfer_{uuid.uuid4().hex}"
        stored_filename = build_stored_filename(
            received_at,
            source or "manual",
            file.filename or "uploaded-file",
            transfer_id,
        )
        final_path = settings.inbox_dir / stored_filename
        temp_path = settings.inbox_dir / f".{transfer_id}.uploading"
        max_bytes = settings.max_file_mb * 1024 * 1024
        size_bytes = 0
        try:
            with temp_path.open("wb") as output:
                while chunk := await file.read(1024 * 1024):
                    size_bytes += len(chunk)
                    if size_bytes > max_bytes:
                        raise HTTPException(status_code=413, detail="File terlalu besar")
                    output.write(chunk)
            digest = sha256_file(temp_path)
            temp_path.rename(final_path)
            append_record(
                _metadata_path(settings),
                TransferRecord(
                    id=transfer_id,
                    received_at=received_at,
                    source=source or "manual",
                    original_filename=file.filename or "uploaded-file",
                    stored_filename=stored_filename,
                    stored_path=str(final_path),
                    size_bytes=size_bytes,
                    sha256=digest,
                    note=note,
                    status="received",
                ),
            )
            return RedirectResponse("/", status_code=303)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    @router.get("/web/files/{transfer_id}/download")
    def web_download(request: Request, transfer_id: str) -> FileResponse:
        _require_login(request)
        record = get_record(_metadata_path(settings), transfer_id)
        if record is None or record.status == "deleted":
            raise HTTPException(status_code=404, detail="File tidak ditemukan")
        return FileResponse(Path(record.stored_path), filename=record.original_filename)

    @router.post("/web/files/{transfer_id}/delete")
    def web_delete(request: Request, transfer_id: str) -> RedirectResponse:
        _require_login(request)
        record = get_record(_metadata_path(settings), transfer_id)
        if record is not None:
            path = Path(record.stored_path)
            if path.exists():
                path.unlink()
            mark_deleted(_metadata_path(settings), transfer_id)
        return RedirectResponse("/", status_code=303)

    return router
```

- [ ] **Step 4: Implement templates**

Create `src/mcp_transfer_node/templates/login.html` with:

```html
<!doctype html>
<html lang="id">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>MCP Transfer Node Login</title>
    <style>
      body { font-family: system-ui, sans-serif; max-width: 720px; margin: 4rem auto; padding: 0 1rem; }
      label, input, button { display: block; width: 100%; margin-top: 0.75rem; }
      input, button { padding: 0.7rem; }
      .error { color: #b00020; font-weight: 700; }
    </style>
  </head>
  <body>
    <main>
      <h1>MCP Transfer Node Login</h1>
      {% if error %}<p class="error">{{ error }}</p>{% endif %}
      <form method="post" action="/login">
        <label for="password">Password admin</label>
        <input id="password" name="password" type="password" autocomplete="current-password" required />
        <button type="submit">Login</button>
      </form>
    </main>
  </body>
</html>
```

Create `src/mcp_transfer_node/templates/index.html` with:

```html
<!doctype html>
<html lang="id">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>MCP Transfer Node - {{ settings.server_name }}</title>
    <style>
      body { font-family: system-ui, sans-serif; max-width: 980px; margin: 2rem auto; padding: 0 1rem; }
      header { display: flex; justify-content: space-between; align-items: center; gap: 1rem; }
      form { margin: 0; }
      .panel { border: 1px solid #ddd; border-radius: 12px; padding: 1rem; margin-top: 1rem; }
      label, input, button { display: block; margin-top: 0.7rem; }
      input[type="text"], input[type="file"] { width: 100%; padding: 0.65rem; }
      table { width: 100%; border-collapse: collapse; margin-top: 1rem; }
      th, td { border-bottom: 1px solid #eee; padding: 0.6rem; text-align: left; vertical-align: top; }
      .actions { display: flex; gap: 0.5rem; }
    </style>
  </head>
  <body>
    <header>
      <h1>MCP Transfer Node - {{ settings.server_name }}</h1>
      <form method="post" action="/logout"><button type="submit">Logout</button></form>
    </header>
    <main>
      <section class="panel" aria-labelledby="upload-heading">
        <h2 id="upload-heading">Upload File</h2>
        <form method="post" action="/web/upload" enctype="multipart/form-data">
          <label for="file">File</label>
          <input id="file" name="file" type="file" required />
          <label for="source">Source</label>
          <input id="source" name="source" type="text" value="manual" />
          <label for="note">Note</label>
          <input id="note" name="note" type="text" />
          <button type="submit">Upload</button>
        </form>
      </section>
      <section class="panel" aria-labelledby="inbox-heading">
        <h2 id="inbox-heading">Inbox</h2>
        <table>
          <thead>
            <tr><th>File</th><th>Source</th><th>Size</th><th>Received</th><th>Actions</th></tr>
          </thead>
          <tbody>
            {% for record in records %}
            <tr data-transfer-id="{{ record.id }}">
              <td>{{ record.original_filename }}</td>
              <td>{{ record.source }}</td>
              <td>{{ record.size_bytes }} bytes</td>
              <td>{{ record.received_at.isoformat() }}</td>
              <td class="actions">
                <a href="/web/files/{{ record.id }}/download">Download</a>
                <form method="post" action="/web/files/{{ record.id }}/delete">
                  <button type="submit">Delete</button>
                </form>
              </td>
            </tr>
            {% endfor %}
          </tbody>
        </table>
      </section>
    </main>
  </body>
</html>
```

- [ ] **Step 5: Mount web router in the app**

Modify `src/mcp_transfer_node/app.py` so it includes the web router:

```python
from __future__ import annotations

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from mcp_transfer_node.api import create_api_router
from mcp_transfer_node.config import TransferSettings, load_settings
from mcp_transfer_node.files import ensure_runtime_dirs
from mcp_transfer_node.logging_config import configure_logging
from mcp_transfer_node.web import create_web_router


def create_app(settings: TransferSettings | None = None) -> FastAPI:
    resolved = settings or load_settings()
    ensure_runtime_dirs(resolved)
    configure_logging(resolved.logs_dir)
    app = FastAPI(title="MCP Transfer Node")
    app.add_middleware(SessionMiddleware, secret_key=resolved.session_secret, max_age=12 * 60 * 60)

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_, exc: HTTPException):
        if isinstance(exc.detail, dict) and "success" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response("HTTP_ERROR", str(exc.detail)),
        )

    app.include_router(create_api_router(resolved))
    app.include_router(create_web_router(resolved))
    return app


def run() -> None:
    uvicorn.run("mcp_transfer_node.app:create_app", factory=True, host="127.0.0.1", port=8787)
```

- [ ] **Step 6: Run web tests to verify they pass**

Run:

```bash
cd /home/fhnasgf/mcp-transfer-node
python -m pytest tests/test_web.py -v
```

Expected: PASS for all web tests.

- [ ] **Step 7: Commit web UI**

Run:

```bash
cd /home/fhnasgf/mcp-transfer-node
git add src/mcp_transfer_node/web.py src/mcp_transfer_node/templates/login.html src/mcp_transfer_node/templates/index.html src/mcp_transfer_node/app.py tests/test_web.py
git commit -m "feat: add simple transfer web ui"
```

---

### Task 5: MCP Tools for Agent File Transfer

**Files:**
- Create: `/home/fhnasgf/mcp-transfer-node/src/mcp_transfer_node/mcp_server.py`
- Test: `/home/fhnasgf/mcp-transfer-node/tests/test_mcp_server.py`

**Interfaces:**
- Consumes: `load_settings`, `load_destinations`, metadata helpers.
- Produces: `resolve_destination(name: str, settings: TransferSettings, env: Mapping[str, str] | None = None) -> ResolvedDestination`.
- Produces: `send_file_to_destination(local_path: Path, destination: str, note: str, settings: TransferSettings, env: Mapping[str, str] | None = None) -> dict[str, object]`.
- Produces MCP tools: `send_file`, `list_received_files`, `get_received_file_info`, `delete_received_file`.

- [ ] **Step 1: Write failing MCP helper tests**

Create `tests/test_mcp_server.py` with:

```python
from pathlib import Path

import httpx
import pytest

from mcp_transfer_node.config import TransferSettings
from mcp_transfer_node.mcp_server import resolve_destination, send_file_to_destination


def test_resolve_destination_loads_url_and_token_from_env(tmp_path: Path) -> None:
    settings = TransferSettings(
        server_name="server-a",
        base_dir=tmp_path,
        max_file_mb=50,
        public_url="https://server-a.clipperyt.online",
        web_admin_password="admin-password",
        session_secret="session-secret-with-more-than-32-chars",
    )
    settings.config_dir.mkdir(parents=True)
    (settings.config_dir / "destinations.json").write_text(
        '{"destinations":[{"name":"server-b","url":"https://server-b.clipperyt.online","tokenEnv":"MCP_TRANSFER_DEST_SERVER_B_TOKEN"}]}',
        encoding="utf-8",
    )

    destination = resolve_destination(
        "server-b",
        settings,
        {"MCP_TRANSFER_DEST_SERVER_B_TOKEN": "secret-token"},
    )

    assert destination.name == "server-b"
    assert destination.url == "https://server-b.clipperyt.online"
    assert destination.token == "secret-token"


@pytest.mark.asyncio
async def test_send_file_to_destination_uploads_bytes_with_headers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = TransferSettings(
        server_name="server-a",
        base_dir=tmp_path,
        max_file_mb=50,
        public_url="https://server-a.clipperyt.online",
        web_admin_password="admin-password",
        session_secret="session-secret-with-more-than-32-chars",
    )
    settings.config_dir.mkdir(parents=True)
    (settings.config_dir / "destinations.json").write_text(
        '{"destinations":[{"name":"server-b","url":"https://server-b.clipperyt.online","tokenEnv":"MCP_TRANSFER_DEST_SERVER_B_TOKEN"}]}',
        encoding="utf-8",
    )
    local_file = tmp_path / "report.txt"
    local_file.write_text("hello", encoding="utf-8")
    captured = {}

    async def fake_post(self, url, files, data, headers):
        captured["url"] = url
        captured["headers"] = headers
        captured["data"] = data
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "transferId": "transfer_abc",
                    "storedFilename": "stored-report.txt",
                    "sha256": "abc123",
                },
                "error": None,
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    result = await send_file_to_destination(
        local_file,
        "server-b",
        "report terbaru",
        settings,
        {"MCP_TRANSFER_DEST_SERVER_B_TOKEN": "secret-token"},
    )

    assert captured["url"] == "https://server-b.clipperyt.online/api/upload"
    assert captured["headers"] == {
        "Authorization": "Bearer secret-token",
        "X-Transfer-Source": "server-a",
    }
    assert captured["data"] == {"note": "report terbaru"}
    assert result["transferId"] == "transfer_abc"
```

- [ ] **Step 2: Run MCP tests to verify they fail**

Run:

```bash
cd /home/fhnasgf/mcp-transfer-node
python -m pytest tests/test_mcp_server.py -v
```

Expected: FAIL because `mcp_server.py` is missing.

- [ ] **Step 3: Implement MCP server helpers and tools**

Create `src/mcp_transfer_node/mcp_server.py` with:

```python
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from mcp_transfer_node.config import TransferSettings, load_destinations, load_settings
from mcp_transfer_node.metadata import get_record, list_records, mark_deleted

mcp = FastMCP("mcp-transfer-node")


@dataclass(frozen=True)
class ResolvedDestination:
    name: str
    url: str
    token: str


def _metadata_path(settings: TransferSettings) -> Path:
    return settings.metadata_dir / "transfers.jsonl"


def _record_to_dict(record) -> dict[str, Any]:
    return {
        "id": record.id,
        "receivedAt": record.received_at.isoformat(),
        "source": record.source,
        "originalFilename": record.original_filename,
        "storedFilename": record.stored_filename,
        "storedPath": record.stored_path,
        "sizeBytes": record.size_bytes,
        "sha256": record.sha256,
        "note": record.note,
        "status": record.status,
    }


def resolve_destination(
    name: str,
    settings: TransferSettings,
    env: Mapping[str, str] | None = None,
) -> ResolvedDestination:
    source = os.environ if env is None else env
    destinations = load_destinations(settings.config_dir / "destinations.json")
    for destination in destinations:
        if destination.name == name:
            token = source.get(destination.token_env)
            if not token:
                raise ValueError(f"missing token env for destination {name}: {destination.token_env}")
            return ResolvedDestination(destination.name, destination.url.rstrip("/"), token)
    if name.startswith("https://"):
        raise ValueError("direct URL destinations require configured aliases to avoid exposing tokens")
    raise ValueError(f"unknown destination: {name}")


async def send_file_to_destination(
    local_path: Path,
    destination: str,
    note: str,
    settings: TransferSettings,
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    if not local_path.exists():
        raise FileNotFoundError(f"local file not found: {local_path}")
    if not local_path.is_file():
        raise ValueError(f"local path is not a regular file: {local_path}")
    max_bytes = settings.max_file_mb * 1024 * 1024
    size_bytes = local_path.stat().st_size
    if size_bytes > max_bytes:
        raise ValueError(f"file exceeds {settings.max_file_mb} MB limit: {size_bytes} bytes")
    resolved = resolve_destination(destination, settings, env)
    headers = {"Authorization": f"Bearer {resolved.token}", "X-Transfer-Source": settings.server_name}
    async with httpx.AsyncClient(timeout=60.0) as client:
        with local_path.open("rb") as file_obj:
            response = await client.post(
                f"{resolved.url}/api/upload",
                files={"file": (local_path.name, file_obj, "application/octet-stream")},
                data={"note": note},
                headers=headers,
            )
    payload = response.json()
    if response.status_code >= 400 or payload.get("success") is not True:
        error = payload.get("error") or {"message": response.text}
        raise RuntimeError(f"destination rejected upload: {error.get('message', 'unknown error')}")
    return dict(payload["data"])


@mcp.tool()
async def send_file(local_path: str, destination: str, note: str = "") -> dict[str, object]:
    """Send a local file to a configured transfer destination."""
    settings = load_settings()
    return await send_file_to_destination(Path(local_path), destination, note, settings)


@mcp.tool()
def list_received_files(limit: int = 20) -> dict[str, object]:
    """List recently received files from the local inbox metadata."""
    settings = load_settings()
    records = [
        _record_to_dict(record)
        for record in list_records(_metadata_path(settings), limit=limit)
        if record.status != "deleted"
    ]
    return {"files": records}


@mcp.tool()
def get_received_file_info(transfer_id: str) -> dict[str, object]:
    """Return metadata for one received transfer."""
    settings = load_settings()
    record = get_record(_metadata_path(settings), transfer_id)
    if record is None:
        raise ValueError(f"transfer not found: {transfer_id}")
    return _record_to_dict(record)


@mcp.tool()
def delete_received_file(transfer_id: str) -> dict[str, object]:
    """Delete one received file and mark it deleted in metadata."""
    settings = load_settings()
    record = get_record(_metadata_path(settings), transfer_id)
    if record is None:
        raise ValueError(f"transfer not found: {transfer_id}")
    path = Path(record.stored_path)
    if path.exists():
        path.unlink()
    changed = mark_deleted(_metadata_path(settings), transfer_id)
    return {"deleted": changed, "transferId": transfer_id}


def run() -> None:
    mcp.run()
```

- [ ] **Step 4: Run MCP tests to verify they pass**

Run:

```bash
cd /home/fhnasgf/mcp-transfer-node
python -m pytest tests/test_mcp_server.py -v
```

Expected: PASS for all MCP helper tests.

- [ ] **Step 5: Commit MCP tools**

Run:

```bash
cd /home/fhnasgf/mcp-transfer-node
git add src/mcp_transfer_node/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: add mcp file transfer tools"
```

---

### Task 6: Runtime Examples, Cloudflare Tunnel Guide, and Full Verification

**Files:**
- Modify: `/home/fhnasgf/mcp-transfer-node/README.md`
- Create: `/home/fhnasgf/mcp-transfer-node/examples/peers.json`
- Create: `/home/fhnasgf/mcp-transfer-node/examples/destinations.json`
- Create: `/home/fhnasgf/mcp-transfer-node/examples/claude_desktop_mcp_config.json`
- Create: `/home/fhnasgf/mcp-transfer-node/tests/test_runtime_security.py`

**Interfaces:**
- Consumes all previous interfaces.
- Produces operator documentation for installing dependencies, creating runtime directories, configuring Cloudflare Tunnel, running the API service, and registering the MCP server.

- [ ] **Step 1: Write runtime security tests**

Create `tests/test_runtime_security.py` with:

```python
from fastapi.testclient import TestClient


def test_path_traversal_filename_never_writes_outside_inbox(client: TestClient) -> None:
    response = client.post(
        "/api/upload",
        files={"file": ("../../.ssh/id_rsa", b"secret-looking-bytes", "application/octet-stream")},
        headers={"Authorization": "Bearer valid-token", "X-Transfer-Source": "server-a"},
    )

    assert response.status_code == 200
    stored_filename = response.json()["data"]["storedFilename"]
    assert ".." not in stored_filename
    assert "/" not in stored_filename
    assert stored_filename.endswith("server-a-id_rsa")


def test_api_error_does_not_echo_token(client: TestClient) -> None:
    response = client.post(
        "/api/upload",
        files={"file": ("hello.txt", b"hello", "text/plain")},
        headers={"Authorization": "Bearer leaked-token-value", "X-Transfer-Source": "server-a"},
    )

    body = response.text
    assert response.status_code == 401
    assert "leaked-token-value" not in body


def test_oversized_file_is_rejected(client: TestClient) -> None:
    too_large = b"x" * (51 * 1024 * 1024)

    response = client.post(
        "/api/upload",
        files={"file": ("big.bin", too_large, "application/octet-stream")},
        headers={"Authorization": "Bearer valid-token", "X-Transfer-Source": "server-a"},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"
```

- [ ] **Step 2: Run runtime security tests to verify behavior**

Run:

```bash
cd /home/fhnasgf/mcp-transfer-node
python -m pytest tests/test_runtime_security.py -v
```

Expected: PASS. If `test_oversized_file_is_rejected` is slow, keep it because it verifies the exact 50 MB boundary in the MVP spec.

- [ ] **Step 3: Add example config files**

Create `examples/peers.json` with:

```json
{
  "allowedPeers": [
    {
      "name": "server-a",
      "tokenHash": "sha256:15d7ce2a3f1f3b2f0126b4d3d8a82ce7e7539a2e6a2b4cfdb0b9f3c0a0f00000",
      "enabled": true
    }
  ]
}
```

Create `examples/destinations.json` with:

```json
{
  "destinations": [
    {
      "name": "server-b",
      "url": "https://server-b.clipperyt.online",
      "tokenEnv": "MCP_TRANSFER_DEST_SERVER_B_TOKEN"
    }
  ]
}
```

Create `examples/claude_desktop_mcp_config.json` with:

```json
{
  "mcpServers": {
    "mcp-transfer-node": {
      "command": "/home/fhnasgf/mcp-transfer-node/.venv/bin/mcp-transfer-mcp",
      "env": {
        "MCP_TRANSFER_SERVER_NAME": "server-a",
        "MCP_TRANSFER_BASE_DIR": "/home/fhnasgf/mcp-transfer",
        "MCP_TRANSFER_MAX_FILE_MB": "50",
        "MCP_TRANSFER_WEB_ADMIN_PASSWORD": "dev-admin-password-change-before-use",
        "MCP_TRANSFER_SESSION_SECRET": "dev-session-secret-32-bytes-minimum-123456",
        "MCP_TRANSFER_PUBLIC_URL": "https://server-a.clipperyt.online",
        "MCP_TRANSFER_DEST_SERVER_B_TOKEN": "dev-peer-token-change-before-use"
      }
    }
  }
}
```

- [ ] **Step 4: Write README setup and verification docs**

Create `README.md` with:

```markdown
# MCP Transfer Node

Direct server-to-server file transfer node with:

- FastAPI HTTP receiver
- Simple human Web UI
- stdio MCP tools for AI agents
- Cloudflare Tunnel exposure model
- API token per peer
- Inbox storage under `/home/fhnasgf/mcp-transfer/inbox/`

## Install

```bash
cd /home/fhnasgf/mcp-transfer-node
python3.11 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
```

## Runtime directories

```bash
mkdir -p /home/fhnasgf/mcp-transfer/{inbox,metadata,config,logs}
cp examples/peers.json /home/fhnasgf/mcp-transfer/config/peers.json
cp examples/destinations.json /home/fhnasgf/mcp-transfer/config/destinations.json
```

Generate a peer token hash:

```bash
python - <<'PY'
from mcp_transfer_node.auth import hash_token
print(hash_token('change-this-peer-token'))
PY
```

Put the printed hash in `/home/fhnasgf/mcp-transfer/config/peers.json` on the receiver server.

## Environment

Create a local `.env` or export variables in your process manager:

```bash
export MCP_TRANSFER_SERVER_NAME=server-a
export MCP_TRANSFER_BASE_DIR=/home/fhnasgf/mcp-transfer
export MCP_TRANSFER_MAX_FILE_MB=50
export MCP_TRANSFER_WEB_ADMIN_PASSWORD='change-this-admin-password'
export MCP_TRANSFER_SESSION_SECRET='change-this-session-secret-32-bytes-minimum'
export MCP_TRANSFER_PUBLIC_URL='https://server-a.clipperyt.online'
export MCP_TRANSFER_DEST_SERVER_B_TOKEN='change-this-peer-token'
```

## Run local service

```bash
cd /home/fhnasgf/mcp-transfer-node
. .venv/bin/activate
mcp-transfer-serve
```

Service binds to:

```text
http://127.0.0.1:8787
```

## Cloudflare Tunnel

Create a Cloudflare Tunnel that maps the server subdomain to the local service:

```text
https://server-a.clipperyt.online -> http://127.0.0.1:8787
```

Farhan adds the DNS/subdomain in Cloudflare manually.

## Web UI

Open:

```text
https://server-a.clipperyt.online
```

Login with `MCP_TRANSFER_WEB_ADMIN_PASSWORD`.

## MCP tool registration

Use `examples/claude_desktop_mcp_config.json` as the shape for MCP registration. The command should point to:

```text
/home/fhnasgf/mcp-transfer-node/.venv/bin/mcp-transfer-mcp
```

## API smoke test

```bash
curl https://server-b.clipperyt.online/health
```

Upload with token:

```bash
curl -X POST https://server-b.clipperyt.online/api/upload \
  -H "Authorization: Bearer change-this-peer-token" \
  -H "X-Transfer-Source: server-a" \
  -F "file=@/home/fhnasgf/report.txt" \
  -F "note=manual curl smoke test"
```

## Test

```bash
cd /home/fhnasgf/mcp-transfer-node
. .venv/bin/activate
python -m pytest -v
python -m ruff check src tests
```
```

- [ ] **Step 5: Run full test and lint suite**

Run:

```bash
cd /home/fhnasgf/mcp-transfer-node
python -m pytest -v
python -m ruff check src tests
```

Expected:

```text
all tests pass
All checks passed!
```

- [ ] **Step 6: Commit docs and runtime examples**

Run:

```bash
cd /home/fhnasgf/mcp-transfer-node
git add README.md examples/peers.json examples/destinations.json examples/claude_desktop_mcp_config.json tests/test_runtime_security.py
git commit -m "docs: add transfer node deployment guide"
```

---

### Task 7: Local End-to-End Verification

**Files:**
- No new source files expected.
- Modify only if verification exposes a defect.

**Interfaces:**
- Consumes complete project from Tasks 1-6.
- Produces verified local MVP behavior before Cloudflare Tunnel setup.

- [ ] **Step 1: Install the project locally**

Run:

```bash
cd /home/fhnasgf/mcp-transfer-node
python3.11 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
```

Expected: install completes without dependency errors.

- [ ] **Step 2: Prepare runtime config**

Run:

```bash
mkdir -p /home/fhnasgf/mcp-transfer/{inbox,metadata,config,logs}
cp /home/fhnasgf/mcp-transfer-node/examples/peers.json /home/fhnasgf/mcp-transfer/config/peers.json
cp /home/fhnasgf/mcp-transfer-node/examples/destinations.json /home/fhnasgf/mcp-transfer/config/destinations.json
```

Expected: the four runtime folders and two config files exist.

- [ ] **Step 3: Start the service**

Run in one terminal:

```bash
cd /home/fhnasgf/mcp-transfer-node
. .venv/bin/activate
export MCP_TRANSFER_SERVER_NAME=server-a
export MCP_TRANSFER_BASE_DIR=/home/fhnasgf/mcp-transfer
export MCP_TRANSFER_MAX_FILE_MB=50
export MCP_TRANSFER_WEB_ADMIN_PASSWORD='change-this-admin-password'
export MCP_TRANSFER_SESSION_SECRET='change-this-session-secret-32-bytes-minimum'
export MCP_TRANSFER_PUBLIC_URL='https://server-a.clipperyt.online'
export MCP_TRANSFER_DEST_SERVER_B_TOKEN='change-this-peer-token'
mcp-transfer-serve
```

Expected: Uvicorn starts on `http://127.0.0.1:8787`.

- [ ] **Step 4: Verify health endpoint**

Run in another terminal:

```bash
curl -s http://127.0.0.1:8787/health
```

Expected response contains:

```json
{"success":true,"data":{"serverName":"server-a","status":"ok","inboxWritable":true,"metadataWritable":true,"maxFileMb":50},"error":null}
```

- [ ] **Step 5: Verify unauthorized upload fails**

Run:

```bash
printf 'hello' > /tmp/mcp-transfer-smoke.txt
curl -s -o /tmp/upload-no-token.json -w '%{http_code}' \
  -X POST http://127.0.0.1:8787/api/upload \
  -H 'X-Transfer-Source: server-a' \
  -F 'file=@/tmp/mcp-transfer-smoke.txt'
```

Expected printed status: `401`.

- [ ] **Step 6: Verify authorized upload succeeds after matching token hash**

Update `/home/fhnasgf/mcp-transfer/config/peers.json` so the peer token hash matches `change-this-peer-token`:

```bash
cd /home/fhnasgf/mcp-transfer-node
. .venv/bin/activate
python - <<'PY'
from pathlib import Path
from mcp_transfer_node.auth import hash_token
path = Path('/home/fhnasgf/mcp-transfer/config/peers.json')
path.write_text('{"allowedPeers":[{"name":"server-a","tokenHash":"' + hash_token('change-this-peer-token') + '","enabled":true}]}', encoding='utf-8')
PY
curl -s -X POST http://127.0.0.1:8787/api/upload \
  -H 'Authorization: Bearer change-this-peer-token' \
  -H 'X-Transfer-Source: server-a' \
  -F 'file=@/tmp/mcp-transfer-smoke.txt' \
  -F 'note=local smoke test'
```

Expected response contains:

```json
"success":true
```

and a `transferId` starting with `transfer_`.

- [ ] **Step 7: Verify file landed in inbox and metadata exists**

Run:

```bash
ls -la /home/fhnasgf/mcp-transfer/inbox
python - <<'PY'
import json
from pathlib import Path
for line in Path('/home/fhnasgf/mcp-transfer/metadata/transfers.jsonl').read_text(encoding='utf-8').splitlines():
    print(json.dumps(json.loads(line), indent=2, sort_keys=True))
PY
```

Expected: one stored file in `inbox/` and one JSON metadata record.

- [ ] **Step 8: Verify all automated checks pass**

Run:

```bash
cd /home/fhnasgf/mcp-transfer-node
. .venv/bin/activate
python -m pytest -v
python -m ruff check src tests
```

Expected: all tests and lint checks pass.

- [ ] **Step 9: Commit verification fixes if needed**

If Step 8 required source changes, run:

```bash
cd /home/fhnasgf/mcp-transfer-node
git add src tests README.md examples
git commit -m "fix: resolve local transfer verification issues"
```

If Step 8 required no source changes, do not create an empty commit.

---

## Self-Review Notes

Spec coverage:

- Direct transfer, public hostnames, Cloudflare Tunnel model: covered in architecture, README, and Task 7 verification.
- API token per peer: covered in Tasks 2, 3, 6, and 7.
- Web upload interface: covered in Task 4.
- MCP tools: covered in Task 5.
- All file types under 50 MB: covered in Tasks 3 and 6.
- Inbox under `/home/fhnasgf/mcp-transfer/inbox/`: covered in config, file helpers, API tests, and verification.
- Metadata JSONL and logging: covered in Tasks 2 and 3.
- Testing plan and acceptance criteria: covered through unit, API, web, MCP, security, and local E2E tasks.

Completion scan: every task has concrete file paths, commands, and code blocks; no unresolved planning markers remain.

Type consistency: later tasks use `TransferSettings`, `AllowedPeer`, `Destination`, `TransferRecord`, and helper function names exactly as defined in earlier tasks.
