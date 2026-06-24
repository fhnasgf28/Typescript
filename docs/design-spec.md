# MCP File Transfer Antar Server — Design Spec

Date: 2026-06-24
Status: Draft for user review
Related project note: [MCP File Transfer Antar Server](../../../02-Projects/MCP%20File%20Transfer%20Antar%20Server.md)

## 1. Purpose

Build a lightweight MCP-enabled file transfer system for sending and receiving files between public servers. The system must let AI agents send files through MCP tools and let humans upload files manually through a simple web interface.

The first version prioritizes functional, secure, direct server-to-server transfer for small files. It should avoid unnecessary relay infrastructure and avoid exposing server ports directly by using Cloudflare Tunnel.

## 2. Confirmed Decisions

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

## 3. High-Level Architecture

Each participating server runs a local service called `mcp-transfer-node`.

The service binds locally, for example:

```text
127.0.0.1:8787
```

Cloudflare Tunnel exposes that local service as a server-specific public URL:

```text
https://server-a.clipperyt.online
https://server-b.clipperyt.online
```

Each node provides three interfaces:

1. **Web UI** — simple browser interface for humans to log in, upload files, view inbox files, download files, and delete files.
2. **HTTP API** — authenticated endpoints for uploads, file listing, downloads, deletion, and health checks.
3. **MCP tools** — tools for AI agents to send files and inspect the local inbox.

Typical transfer flow:

```text
AI agent on Server A
  -> MCP tool send_file
  -> reads local file on Server A
  -> POST HTTPS to https://server-b.clipperyt.online/api/upload
  -> Server B validates bearer token
  -> Server B stores file under /home/fhnasgf/mcp-transfer/inbox/
  -> Server B writes metadata and log entry
```

## 4. Components

### 4.1 Receiver HTTP API

Initial endpoints:

- `GET /health`
- `POST /api/upload`
- `GET /api/files`
- `GET /api/files/:id/download`
- `DELETE /api/files/:id`

All `/api/*` endpoints require authentication. `GET /health` may be public but must not reveal secrets.

### 4.2 Web UI

The initial Web UI is intentionally simple:

- Login page.
- Upload form.
- Inbox list.
- Download action.
- Delete action.

The UI is not expected to be visually elaborate in the MVP. It must provide clear success and failure messages.

Example layout:

```text
MCP Transfer Node - server-b

[Upload File]
File: [Choose file]
Source: [manual]
Note: [optional]
[Upload]

[Inbox]
- report.txt | manual | 12 KB | received at ... | [Download] [Delete]
```

### 4.3 MCP Tools

Initial MCP tools:

- `send_file`
  - Sends a local file to a configured destination node.
  - Accepts either a destination alias or a destination URL.
  - Uses configured outbound token, not a token pasted into prompts.
- `list_received_files`
  - Lists recent received files from metadata.
- `get_received_file_info`
  - Returns metadata for one transfer ID.
- `delete_received_file`
  - Deletes one received file and marks metadata as deleted.

## 5. Security Model

### 5.1 Peer API Tokens

Each receiver has an allowlist of peer tokens. Tokens identify the peer allowed to upload to that receiver.

Upload requests use:

```http
Authorization: Bearer <peer-token>
X-Transfer-Source: server-a
```

The receiver validates:

1. A bearer token is present.
2. The token matches an enabled allowed peer.
3. The source header matches the peer identity associated with the token.

If validation fails, the receiver returns `401 Unauthorized` or `403 Forbidden` with a safe error message.

### 5.2 Token Storage

- Raw tokens must not be committed to git.
- Outbound destination tokens are loaded from environment variables or a local ignored secret file.
- Receiver-side peer tokens should be stored as hashes when practical.
- Logs must never include raw tokens.
- Rotating one peer token must not affect other peers.

### 5.3 Web Authentication

The Web UI uses separate human credentials from API peer tokens.

Initial model:

- `MCP_TRANSFER_WEB_ADMIN_PASSWORD` in environment.
- `MCP_TRANSFER_SESSION_SECRET` in environment.
- Session expiry, for example 12 hours.
- Basic CSRF protection for state-changing web forms.

### 5.4 File Safety

The service accepts all file types, but with strict handling:

- Maximum file size: 50 MB.
- Only regular files are accepted.
- Uploaded content is never executed.
- Uploaded content is stored as binary data.
- Original filenames are sanitized.
- Any path component from the sender is ignored.
- Files are never written outside the configured inbox.
- Existing files are never overwritten.

Path traversal attempts such as `../../.ssh/id_rsa` or absolute paths such as `/etc/passwd` must not influence the storage path.

## 6. Storage and Configuration

### 6.1 Data Root

Initial data root:

```text
/home/fhnasgf/mcp-transfer/
```

Structure:

```text
/home/fhnasgf/mcp-transfer/
├── inbox/
│   └── 2026-06-24T210501Z-server-a-report.txt
├── metadata/
│   └── transfers.jsonl
├── config/
│   └── peers.json
└── logs/
    └── app.log
```

### 6.2 Environment Configuration

Example environment variables:

```env
MCP_TRANSFER_SERVER_NAME=server-b
MCP_TRANSFER_BASE_DIR=/home/fhnasgf/mcp-transfer
MCP_TRANSFER_MAX_FILE_MB=50
MCP_TRANSFER_WEB_ADMIN_PASSWORD=<local-admin-password-set-during-deploy>
MCP_TRANSFER_SESSION_SECRET=<random-32-byte-session-secret-set-during-deploy>
MCP_TRANSFER_PUBLIC_URL=https://server-b.clipperyt.online
```

Outbound destination token example:

```env
MCP_TRANSFER_DEST_SERVER_B_TOKEN=<random-peer-token-set-during-deploy>
```

### 6.3 Receiver Peer Config

Example `config/peers.json`:

```json
{
  "allowedPeers": [
    {
      "name": "server-a",
      "tokenHash": "sha256:...",
      "enabled": true
    },
    {
      "name": "laptop-farhan",
      "tokenHash": "sha256:...",
      "enabled": true
    }
  ]
}
```

### 6.4 Destination Config

MCP `send_file` should support destination aliases so agents do not need to handle raw tokens.

Example:

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

Then an agent can call:

```text
send_file(local_path="/home/fhnasgf/report.txt", destination="server-b")
```

## 7. Metadata and Audit Log

Each accepted transfer writes a metadata record.

Example:

```json
{
  "id": "transfer_01J...",
  "receivedAt": "2026-06-24T21:05:01Z",
  "source": "server-a",
  "originalFilename": "report.txt",
  "storedFilename": "2026-06-24T210501Z-server-a-report.txt",
  "storedPath": "/home/fhnasgf/mcp-transfer/inbox/2026-06-24T210501Z-server-a-report.txt",
  "sizeBytes": 12345,
  "sha256": "abc...",
  "note": "report terbaru",
  "status": "received"
}
```

Metadata is initially stored in append-only JSONL at:

```text
/home/fhnasgf/mcp-transfer/metadata/transfers.jsonl
```

SQLite can be introduced later if query needs grow.

Application logs go to:

```text
/home/fhnasgf/mcp-transfer/logs/app.log
```

Log events include service start, accepted upload, rejected upload, auth failure, delete action, config load error, and uncaught errors. Logs must not contain raw tokens.

## 8. Filename Policy

Stored filename format:

```text
<timestamp>-<source>-<safe-original-name>
```

Example:

```text
2026-06-24T210501Z-server-a-report.txt
```

Rules:

- Strip all directory components from the submitted filename.
- Replace unsafe characters with `-`.
- Preserve useful extensions where possible.
- Add a suffix or transfer ID if a collision occurs.
- Never overwrite an existing file.

## 9. API Response Format

Successful response:

```json
{
  "success": true,
  "data": {
    "transferId": "transfer_01J...",
    "storedFilename": "2026-...",
    "sha256": "abc..."
  },
  "error": null
}
```

Error response:

```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "UNAUTHORIZED",
    "message": "Invalid or missing bearer token"
  }
}
```

Errors must be explicit enough for agents to explain the issue but must not leak secrets or internal stack traces.

## 10. Error Handling

`send_file` must handle and report at least these failures:

- Local file not found.
- Local path is a directory.
- File exceeds 50 MB.
- Destination alias is unknown.
- Destination health check fails.
- Destination rejects credentials.
- Destination upload times out.
- Destination returns malformed response.

Web UI must show clear messages for:

- Invalid password.
- Missing file selection.
- Oversized file.
- Upload success.
- Upload failure.
- Download failure.
- Delete success/failure.

Upload implementation should use a safe sequence:

1. Stream upload to a temporary file inside the data root.
2. Enforce max size while receiving.
3. Hash the file.
4. Atomically rename to final inbox path.
5. Append metadata.
6. Remove temporary files on failure.

## 11. Health Check

`GET /health` response:

```json
{
  "success": true,
  "data": {
    "serverName": "server-b",
    "status": "ok",
    "inboxWritable": true,
    "metadataWritable": true,
    "maxFileMb": 50
  },
  "error": null
}
```

The health check is used to confirm the local service and Cloudflare Tunnel path are working.

## 12. Testing Plan

### 12.1 Unit Tests

Cover:

- Filename sanitization.
- Stored filename generation.
- File size validation.
- Token hashing and verification.
- Config parsing.
- API response formatting.
- Metadata JSONL append/read.

### 12.2 Local Integration Tests

Against `http://127.0.0.1:8787`:

- `GET /health` succeeds.
- Upload without token is rejected.
- Upload with wrong token is rejected.
- Upload with valid token succeeds.
- File lands in inbox.
- Metadata is written.
- File list includes the new file.
- Download returns identical bytes.
- Delete removes the file and updates metadata/status.

### 12.3 MCP Tool Tests

Cover:

- `send_file` succeeds to local receiver.
- `send_file` rejects missing file.
- `send_file` rejects directories.
- `send_file` rejects file over 50 MB.
- `list_received_files` reads metadata correctly.
- `get_received_file_info` returns the right transfer.
- `delete_received_file` deletes only the selected transfer.

### 12.4 Web UI Smoke Tests

Cover:

- Login page loads.
- Wrong password fails.
- Correct password logs in.
- Upload file succeeds.
- Inbox shows uploaded file.
- Download works.
- Delete works.

### 12.5 Two-Server End-to-End Test

With two server subdomains:

1. Verify `GET https://server-b.clipperyt.online/health`.
2. From Server A, use MCP `send_file` to send a file to `server-b`.
3. Confirm file appears on Server B via Web UI.
4. Confirm file appears through `list_received_files`.
5. Confirm file exists under `/home/fhnasgf/mcp-transfer/inbox/`.
6. Compare source and destination SHA-256.
7. Confirm invalid token returns `401`.
8. Confirm file over 50 MB is rejected.

## 13. MVP Scope

Included in MVP:

- Local service with HTTP API.
- Cloudflare Tunnel deployment model.
- Simple web upload/list/download/delete UI.
- API token per peer.
- Web admin password.
- Inbox storage under `/home/fhnasgf/mcp-transfer/inbox/`.
- All file types accepted as binary data under 50 MB.
- Metadata JSONL.
- Basic app log.
- MCP tools for send/list/info/delete.

Not included in MVP:

- Chunked uploads.
- Resume uploads.
- Folder uploads.
- Antivirus scanning.
- Multi-user web accounts.
- Role-based access control.
- Central relay.
- Automatic bidirectional sync.
- Automatic Cloudflare DNS/tunnel provisioning.

## 14. Acceptance Criteria

The MVP is complete when:

- An AI agent can send a small file from Server A to Server B with one MCP tool call.
- A human can upload a file through the browser to a server node.
- Received files are stored under `/home/fhnasgf/mcp-transfer/inbox/`.
- All file types are accepted as binary files if they are under 50 MB.
- Each transfer writes metadata and log entries.
- API tokens are configurable per peer.
- Invalid tokens are rejected.
- Web login protects manual upload and inbox actions.
- The service works behind Cloudflare Tunnel on a `clipperyt.online` subdomain.
- Tests verify auth, upload, listing, download, delete, path safety, file size limit, and MCP send behavior.
