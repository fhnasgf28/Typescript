# OpenClaw Spreadsheet MCP

MCP stdio server kecil untuk membaca Google Sheet task tracker publik dan mengembalikan hasil ringkas dari tab Bug Tracker All.

Fokus default:

- Sheet: Bug Tracker All
- Assignee: Farhan
- Open task: kolom Dev Status kosong
- Link attachment ikut ditampilkan agar task bisa langsung dibuka, dengan output tetap dibatasi agar tidak boros token.

Tools:

- get_farhan_open_dev_tasks
- summarize_farhan_bug_tracker
- search_farhan_bug_tracker
- update_farhan_bug_tracker_task

Run manual:

    node /home/adminftp/farhan/openclaw-spreadsheet-mcp/server.mjs

OpenClaw registration memakai:

    openclaw mcp add hmx-bug-tracker-sheets --command /home/adminftp/.nvm/versions/node/v22.22.3/bin/node --arg /home/adminftp/farhan/openclaw-spreadsheet-mcp/server.mjs --include get_farhan_open_dev_tasks,summarize_farhan_bug_tracker,search_farhan_bug_tracker,update_farhan_bug_tracker_task --parallel

Write support:

- `update_farhan_bug_tracker_task` memakai nomor `row` dari hasil read/search.
- Bisa mengisi `devStatus`, append `note`, append `commit`, append `link`, atau mengganti `attachment`.
- Jalankan dengan `dryRun: true` dulu untuk preview range dan field yang akan diubah.
- Update nyata butuh Google service-account JSON di `/home/adminftp/.config/openclaw-sheet-tasks/google-service-account.json` atau env `HMX_BUG_TRACKER_GOOGLE_CREDENTIALS`.
- Share spreadsheet sebagai Editor ke email service account tersebut.
