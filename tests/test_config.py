from pathlib import Path

import pytest

from mcp_transfer_node.config import (
    load_allowed_peers,
    load_destinations,
    load_settings,
)


def test_load_settings_uses_environment_values() -> None:
    base_dir = Path("/home/fhnasgf/mcp-transfer-test")
    env = {
        "MCP_TRANSFER_SERVER_NAME": "server-b",
        "MCP_TRANSFER_BASE_DIR": str(base_dir),
        "MCP_TRANSFER_MAX_FILE_MB": "50",
        "MCP_TRANSFER_WEB_ADMIN_PASSWORD": "admin-password",
        "MCP_TRANSFER_SESSION_SECRET": "session-secret-with-more-than-32-chars",
        "MCP_TRANSFER_PUBLIC_URL": "https://server-b.clipperyt.online",
        "MCP_TRANSFER_WEB_ADMIN_USERNAME": "Farhan",
    }

    settings = load_settings(env)

    assert settings.server_name == "server-b"
    assert settings.base_dir == base_dir
    assert settings.max_file_mb == 50
    assert settings.web_admin_password == "admin-password"
    assert settings.web_admin_username == "Farhan"
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


def test_load_settings_accepts_custom_home_allowlist_prefix(tmp_path: Path) -> None:
    base_dir = tmp_path / "runtime"
    env = {
        "MCP_TRANSFER_SERVER_NAME": "server-b",
        "MCP_TRANSFER_HOME_ALLOWLIST_PREFIX": str(tmp_path),
        "MCP_TRANSFER_BASE_DIR": str(base_dir),
        "MCP_TRANSFER_MAX_FILE_MB": "50",
        "MCP_TRANSFER_WEB_ADMIN_PASSWORD": "admin-password",
        "MCP_TRANSFER_SESSION_SECRET": "session-secret-with-more-than-32-chars",
        "MCP_TRANSFER_PUBLIC_URL": "https://server-b.clipperyt.online",
    }

    settings = load_settings(env)

    assert settings.base_dir == base_dir


def test_load_settings_google_docs_is_optional_and_bounded(tmp_path: Path) -> None:
    base = {
        "MCP_TRANSFER_SERVER_NAME": "server-b",
        "MCP_TRANSFER_HOME_ALLOWLIST_PREFIX": str(tmp_path),
        "MCP_TRANSFER_BASE_DIR": str(tmp_path / "runtime"),
        "MCP_TRANSFER_WEB_ADMIN_PASSWORD": "admin-password",
        "MCP_TRANSFER_SESSION_SECRET": "session-secret-with-more-than-32-chars",
        "MCP_TRANSFER_PUBLIC_URL": "https://server-b.clipperyt.online",
    }
    assert load_settings(base).google_docs_service_account_file is None

    credential = tmp_path / "owner-only.json"
    configured = load_settings(
        {
            **base,
            "MCP_PMT_GOOGLE_DOCS_SERVICE_ACCOUNT_FILE": str(credential),
            "MCP_PMT_GOOGLE_DOCS_TIMEOUT_SECONDS": "12.5",
        }
    )
    assert configured.google_docs_service_account_file == credential
    assert configured.google_docs_timeout_seconds == 12.5

    with pytest.raises(ValueError, match="absolute path"):
        load_settings({**base, "MCP_PMT_GOOGLE_DOCS_SERVICE_ACCOUNT_FILE": "relative.json"})
    with pytest.raises(ValueError, match="between 3 and 60"):
        load_settings({**base, "MCP_PMT_GOOGLE_DOCS_TIMEOUT_SECONDS": "61"})


def test_drive_watch_defaults_off_and_validates_complete_same_origin_config(tmp_path: Path) -> None:
    base = {
        "MCP_TRANSFER_SERVER_NAME": "server-b",
        "MCP_TRANSFER_HOME_ALLOWLIST_PREFIX": str(tmp_path),
        "MCP_TRANSFER_BASE_DIR": str(tmp_path / "runtime"),
        "MCP_TRANSFER_WEB_ADMIN_PASSWORD": "admin-password",
        "MCP_TRANSFER_SESSION_SECRET": "session-secret-with-more-than-32-chars",
        "MCP_TRANSFER_PUBLIC_URL": "https://pmt.example.com",
    }
    assert load_settings(base).pmt_drive_watch_enabled is False
    enabled = {
        **base,
        "MCP_PMT_DRIVE_WATCH_ENABLED": "true",
        "MCP_PMT_DRIVE_SPREADSHEET_ID": "sheet_123",
        "MCP_PMT_DRIVE_CSV_URL": (
            "https://docs.google.com/spreadsheets/d/sheet_123/export?format=csv&gid=0"
        ),
        "MCP_PMT_DRIVE_WEBHOOK_SECRET": "x" * 32,
        "MCP_PMT_GOOGLE_DOCS_SERVICE_ACCOUNT_FILE": str(tmp_path / "credential.json"),
    }
    configured = load_settings(enabled)
    assert configured.pmt_drive_webhook_callback_url == (
        "https://pmt.example.com/api/v1/pmt/drive-notifications/bug-tracker"
    )

    bad_values = (
        {"MCP_PMT_DRIVE_WEBHOOK_SECRET": "short"},
        {"MCP_PMT_DRIVE_SPREADSHEET_ID": "different"},
        {
            "MCP_PMT_DRIVE_WEBHOOK_CALLBACK_URL": "https://evil.test/api/v1/pmt/drive-notifications/bug-tracker"
        },
        {
            "MCP_PMT_DRIVE_WEBHOOK_CALLBACK_URL": "https://pmt.example.com:443/api/v1/pmt/drive-notifications/bug-tracker"
        },
        {
            "MCP_PMT_DRIVE_WEBHOOK_CALLBACK_URL": "https://user@pmt.example.com/api/v1/pmt/drive-notifications/bug-tracker"
        },
    )
    for override in bad_values:
        with pytest.raises(ValueError):
            load_settings({**enabled, **override})


def test_legacy_http_public_url_is_allowed_only_when_drive_watch_disabled(tmp_path: Path) -> None:
    base = {
        "MCP_TRANSFER_SERVER_NAME": "server-b",
        "MCP_TRANSFER_HOME_ALLOWLIST_PREFIX": str(tmp_path),
        "MCP_TRANSFER_BASE_DIR": str(tmp_path / "runtime"),
        "MCP_TRANSFER_WEB_ADMIN_PASSWORD": "admin-password",
        "MCP_TRANSFER_SESSION_SECRET": "session-secret-with-more-than-32-chars",
        "MCP_TRANSFER_PUBLIC_URL": "http://127.0.0.1:8787/",
    }
    assert load_settings(base).public_url == "http://127.0.0.1:8787"
    with pytest.raises(ValueError, match="exact HTTPS origin"):
        load_settings(
            {
                **base,
                "MCP_PMT_DRIVE_WATCH_ENABLED": "true",
                "MCP_PMT_DRIVE_SPREADSHEET_ID": "sheet_123",
                "MCP_PMT_DRIVE_CSV_URL": (
                    "https://docs.google.com/spreadsheets/d/sheet_123/export?format=csv"
                ),
                "MCP_PMT_DRIVE_WEBHOOK_SECRET": "x" * 32,
                "MCP_PMT_GOOGLE_DOCS_SERVICE_ACCOUNT_FILE": str(tmp_path / "credential.json"),
            }
        )


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
        '{"allowedPeers":[{"name":"server-a","tokenHash":"sha256:abc","enabled":true,'
        '"scopes":["approval.execute:chat_message"]}]}',
        encoding="utf-8",
    )

    peers = load_allowed_peers(config_path)

    assert peers[0].name == "server-a"
    assert peers[0].token_hash == "sha256:abc"
    assert peers[0].enabled is True
    assert peers[0].scopes == ("approval.execute:chat_message",)
