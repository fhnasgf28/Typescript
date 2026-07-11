#!/usr/bin/env bash
set -euo pipefail
NODE_BIN="${NODE_BIN:-/home/adminftp/.nvm/versions/node/v22.22.3/bin/node}"

openclaw mcp add hmx-mr-log   --command "$NODE_BIN"   --arg /home/adminftp/farhan/openclaw-mr-log-mcp/server.mjs   --include check_farhan_mr_log_setup,get_farhan_recent_gitlab_mrs,find_unlogged_farhan_mrs,log_farhan_mr_to_sheet,sync_farhan_unlogged_mrs_to_sheet,create_mr_log_backup_checkpoint,list_mr_log_backups,restore_mr_log_backup   --parallel

openclaw mcp add hmx-bug-tracker-sheets   --command "$NODE_BIN"   --arg /home/adminftp/farhan/openclaw-spreadsheet-mcp/server.mjs   --include get_farhan_open_dev_tasks,summarize_farhan_bug_tracker,search_farhan_bug_tracker,mark_farhan_task_done,update_farhan_bug_tracker_task   --parallel
