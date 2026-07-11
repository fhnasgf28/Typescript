#!/usr/bin/env bash
set -euo pipefail
BASE_HOME="${BASE_HOME:-/home/adminftp}"
FARHAN_DIR="${FARHAN_DIR:-$BASE_HOME/farhan}"

mkdir -p "$FARHAN_DIR/openclaw-mr-log-mcp" "$FARHAN_DIR/openclaw-spreadsheet-mcp"
cp -a "$(dirname "$0")/mcp/hmx-mr-log-mcp/." "$FARHAN_DIR/openclaw-mr-log-mcp/"
cp -a "$(dirname "$0")/mcp/hmx-bug-tracker-sheets/." "$FARHAN_DIR/openclaw-spreadsheet-mcp/"
chmod +x "$FARHAN_DIR/openclaw-mr-log-mcp/server.mjs" "$FARHAN_DIR/openclaw-mr-log-mcp/xlsx_helper.py" "$FARHAN_DIR/openclaw-spreadsheet-mcp/server.mjs"

mkdir -p "$BASE_HOME/.config/openclaw-mr-log" "$BASE_HOME/.config/openclaw-sheet-tasks" "$BASE_HOME/.local/share/openclaw-mr-log/backups"
chmod 700 "$BASE_HOME/.config/openclaw-mr-log" "$BASE_HOME/.config/openclaw-sheet-tasks" "$BASE_HOME/.local/share/openclaw-mr-log" "$BASE_HOME/.local/share/openclaw-mr-log/backups"

if [ ! -f "$BASE_HOME/.config/openclaw-mr-log/config.json" ]; then
  cp "$(dirname "$0")/config-templates/openclaw-mr-log.config.example.json" "$BASE_HOME/.config/openclaw-mr-log/config.json"
  chmod 600 "$BASE_HOME/.config/openclaw-mr-log/config.json"
fi

cat <<'MSG'
Installed MCP source files.
Next required manual steps on this destination server:
1. Fill /home/adminftp/.config/openclaw-mr-log/config.json placeholders.
2. Put GitLab token at /home/adminftp/.config/openclaw-mr-log/gitlab-token with chmod 600.
3. Put Google service-account JSON files at the credential paths documented in HANDOFF.md with chmod 600.
4. Run config-templates/openclaw-mcp-add-commands.sh from this extracted bundle.
MSG
