#!/usr/bin/env bash
set -euo pipefail
BASE=${BASE:-/home/adminftp/farhan}
NODE=${NODE:-/home/adminftp/.nvm/versions/node/v22.22.3/bin/node}
NPM=${NPM:-/home/adminftp/.nvm/versions/node/v22.22.3/bin/npm}
CONF=/home/adminftp/.config
SHARE=/home/adminftp/.local/share
mkdir -p "$BASE" "$CONF/openclaw-hashmicro-chat" "$CONF/openclaw-internal-status" "$CONF/openclaw-mr-log" "$CONF/openclaw-spreadsheet-mcp" "$SHARE/openclaw-hashmicro-chat" "$SHARE/openclaw-mr-log/state" "$SHARE/openclaw-mr-log/backups" "$SHARE/openclaw-bug-tracker"
cp -a mcp/openclaw-hashmicro-chat-mcp "$BASE/"
cp -a mcp/openclaw-internal-status-mcp "$BASE/"
cp -a mcp/openclaw-mr-log-mcp "$BASE/"
cp -a mcp/openclaw-spreadsheet-mcp "$BASE/"
[ -f scripts/hashchat-relogin ] && install -m 700 scripts/hashchat-relogin /home/adminftp/.local/bin/hashchat-relogin
install -m 600 config-templates/openclaw-hashmicro-chat.config.json "$CONF/openclaw-hashmicro-chat/config.json"
install -m 600 config-templates/openclaw-internal-status.config.json "$CONF/openclaw-internal-status/config.json"
install -m 600 config-templates/openclaw-mr-log.config.json "$CONF/openclaw-mr-log/config.json"
install -m 600 config-templates/openclaw-spreadsheet-mcp.config.json "$CONF/openclaw-spreadsheet-mcp/config.json"
if [ -f "$BASE/openclaw-hashmicro-chat-mcp/package.json" ]; then
  (cd "$BASE/openclaw-hashmicro-chat-mcp" && "$NPM" install)
  (cd "$BASE/openclaw-hashmicro-chat-mcp" && "$NODE" node_modules/playwright/cli.js install chromium || true)
fi
for f in "$BASE"/openclaw-*mcp/server.mjs; do "$NODE" --check "$f"; done
python3 -m py_compile "$BASE/openclaw-mr-log-mcp/xlsx_helper.py"
echo "install_ok=true"
echo "next: fill config placeholders, add GitLab token and Google service-account JSON, then register MCP using openclaw/mcp-registration.example.json"
