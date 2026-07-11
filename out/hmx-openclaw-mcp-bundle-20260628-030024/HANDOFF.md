# HMX OpenClaw MCP Bundle Handoff

Bundle ini berisi dua MCP stdio server untuk dipakai di server OpenClaw lain:

- `hmx-mr-log`: sync MR Farhan dari GitLab ke sheet HR MR Log.
- `hmx-bug-tracker-sheets`: baca/update task Farhan dari sheet Bug Tracker All.

## Isi Paket

- `mcp/hmx-mr-log-mcp/server.mjs`
- `mcp/hmx-mr-log-mcp/xlsx_helper.py`
- `mcp/hmx-bug-tracker-sheets/server.mjs`
- `mcp/hmx-bug-tracker-sheets/README.md`
- `config-templates/openclaw-mr-log.config.example.json`
- `config-templates/hmx-bug-tracker.env.example`
- `config-templates/openclaw-mcp-add-commands.sh`
- `install.sh`
- `checksums.sha256`

## Secret Yang Tidak Dibundel

Jangan taruh credential di repo/paket. Siapkan manual di server tujuan:

- GitLab token: `/home/adminftp/.config/openclaw-mr-log/gitlab-token`
- MR Log Google service account: `/home/adminftp/.config/openclaw-mr-log/google-service-account.json`
- Bug Tracker Google service account: `/home/adminftp/.config/openclaw-sheet-tasks/google-service-account.json`

Semua file credential sebaiknya mode `600`, folder config mode `700`.

## Install Di Server Tujuan

```bash
cd /home/adminftp/farhan
unzip hmx-openclaw-mcp-bundle-*.zip
cd hmx-openclaw-mcp-bundle-*
./install.sh
```

Setelah itu isi placeholder di:

```bash
/home/adminftp/.config/openclaw-mr-log/config.json
```

Untuk bug tracker, pastikan env berikut tersedia di runtime OpenClaw/server:

```bash
HMX_BUG_TRACKER_SPREADSHEET_ID=...
HMX_BUG_TRACKER_SHEET_NAME="Bug Tracker All"
HMX_BUG_TRACKER_ASSIGNEE="Farhan"
HMX_BUG_TRACKER_GOOGLE_CREDENTIALS=/home/adminftp/.config/openclaw-sheet-tasks/google-service-account.json
```

## Register MCP Ke OpenClaw

```bash
./config-templates/openclaw-mcp-add-commands.sh
```

Jika Node path berbeda, jalankan:

```bash
NODE_BIN=/path/to/node ./config-templates/openclaw-mcp-add-commands.sh
```

## MR Log Rules

- Fokus sheet `HR`.
- Tulis hanya 5 kolom utama: `Tanggal MR`, `MR Title`, `MR Link`, `Developer Name`, `SA Name`.
- Date format memakai `M/D/YYYY`, contoh `6/26/2026`.
- `Developer Name` ditulis `Farhan`, bukan username GitLab.
- Source branch default: `feat/loan-policy-autocode-clean`.
- Target branch default: `Human-Resources`.
- Gunakan dry-run dulu sebelum write.
- Tool sync otomatis membuat checkpoint lokal sebelum write supaya perubahan terakhir bisa di-restore cepat tanpa scan full sheet.

## Bug Tracker Rules

- Fokus sheet `Bug Tracker All`.
- Fokus assignee `Farhan`.
- Untuk update task, ambil row number dari hasil read/search MCP, lalu update row tersebut.
- Pakai `dryRun: true` dulu kecuali user eksplisit meminta update langsung.

## Quick Verify

```bash
node /home/adminftp/farhan/openclaw-mr-log-mcp/server.mjs
node /home/adminftp/farhan/openclaw-spreadsheet-mcp/server.mjs
```

Untuk stdio MCP, proses akan menunggu JSON-RPC input. Stop dengan `Ctrl+C` jika hanya mengecek startup.
