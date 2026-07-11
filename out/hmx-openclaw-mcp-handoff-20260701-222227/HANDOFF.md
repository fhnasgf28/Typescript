# HMX OpenClaw MCP Handoff

Prepared for transferring Farhan Assegaf's OpenClaw MCP setup to another OpenClaw server.

## Included MCPs

- `openclaw-hashmicro-chat-mcp` — HashMicro Chat bridge for internal status drafts and send flow.
- `openclaw-internal-status-mcp` — morning/evening internal status draft generator from Bug Tracker and MR context.
- `openclaw-mr-log-mcp` — GitLab MR log sync, XLSX write, rollback checkpoint, and Farhan-only cache.
- `openclaw-spreadsheet-mcp` — Bug Tracker All task tools, cache, and Internal Status marker L/M/N updater.

## Security Boundary

Credential files, tokens, service-account JSON, cookies, browser profile, local cache, rollback XLSX backups, `.env`, and private runtime state are intentionally NOT included in this ZIP. Re-create them on the destination server.

Required destination-side secrets:

- `/home/adminftp/.config/openclaw-mr-log/gitlab-token`
- `/home/adminftp/.config/openclaw-mr-log/google-service-account.json`
- HashMicro Chat login session, created by `/home/adminftp/.local/bin/hashchat-relogin` or by MCP tool `setup_hashchat_browser_session`
- Spreadsheet IDs in config templates

## Install

```bash
cd /home/adminftp/farhan
unzip hmx-openclaw-mcp-handoff-*.zip
cd hmx-openclaw-mcp-handoff-*
./install.sh
```

After install, fill placeholders in:

```text
/home/adminftp/.config/openclaw-hashmicro-chat/config.json
/home/adminftp/.config/openclaw-internal-status/config.json
/home/adminftp/.config/openclaw-mr-log/config.json
/home/adminftp/.config/openclaw-spreadsheet-mcp/config.json
```

Then merge the MCP registration snippet from:

```text
openclaw/mcp-registration.example.json
```

into `/home/adminftp/.openclaw/openclaw.json` on the destination server.

## HashMicro Chat Relogin

Use this interactive command as `adminftp`:

```bash
/home/adminftp/.local/bin/hashchat-relogin
```

It asks for username/password, writes a temporary private credential file, runs `setup_hashchat_browser_session`, and removes the temporary credential file after successful login.

## Verify

```bash
export PATH=/home/adminftp/.nvm/versions/node/v22.22.3/bin:$PATH
openclaw mcp reload hmx-hashmicro-chat
openclaw mcp reload hmx-internal-status
openclaw mcp reload hmx-mr-log
openclaw mcp reload hmx-bug-tracker-sheets
openclaw mcp probe hmx-hashmicro-chat
openclaw mcp probe hmx-internal-status
openclaw mcp probe hmx-mr-log
openclaw mcp probe hmx-bug-tracker-sheets
```

Expected tool counts may vary by OpenClaw filter config, but all four servers should probe without diagnostics.

## Notes

- Keep `sendEnabled=false` on HashMicro Chat until `get_hashchat_connector_status` reports the session is ready and the target group has been verified.
- Keep MR log `writeColumnLimit=5` so only the first five HR sheet columns are written automatically.
- Bug tracker Internal Status markers are columns L/M/N: `Internal Status`, `Internal Status Date`, `Internal Status Note`.
