# Standalone PMT MVP

## Purpose

Standalone PMT adds a central task-orchestration layer to MCP Transfer Node. One PMT website/API can coordinate multiple OpenClaw installations without allowing two agents to silently own the same task.

The MVP is deliberately small:

- authenticated Web dashboard and task creation
- versioned agent REST API
- remote stdio MCP adapter for each OpenClaw server
- SQLite persistence with WAL mode
- atomic task claim, bounded lease, heartbeat, idempotency, and audit events
- durable interval schedules with worker leases
- a bounded Google Sheet `To-Do` importer

It does **not** push branches, create merge requests, send chat messages, deploy, or write task status back to Google Sheet. Those remain explicit approval-gated follow-up integrations.

## Architecture

```text
                         HTTPS
 OpenClaw server A ── MCP adapter ──┐
                                    │
 OpenClaw server B ── MCP adapter ──┼── PMT FastAPI ── SQLite/WAL
                                    │       │
 Human browser ─────────────────────┘       ├── Task dashboard
                                            └── Schedule worker
                                                   │
                                                   └── Google Sheet CSV (read only)
```

The REST API is the cross-server contract. The MCP process is a thin authenticated client and does not need shared filesystem or database access.

## Task lifecycle

```text
todo -> claimed -> in_progress -> ready_for_review
                    |      |
                    |      +-> blocked -> in_progress
                    +-> todo (release)
```

`done` and `cancelled` are supported in storage/API, but deployments should reserve them for human approval or a policy-controlled automation.

### Claim guarantees

`POST /api/v1/pmt/tasks/{key}/claim` runs in a SQLite `BEGIN IMMEDIATE` transaction.

- one active owner per task
- claim lease of 60–7,200 seconds
- idempotency key makes retries safe
- another agent receives `409 CLAIM_CONFLICT`
- an expired lease may be reclaimed
- heartbeat extends the lease

For the current HMX workflow, use a 30–60 minute lease and heartbeat during long-running verification.

## Authentication model

The MVP reuses `config/peers.json` identities. Each OpenClaw server must have a unique peer entry and token.

Example central `peers.json`:

```json
{
  "allowedPeers": [
    {
      "name": "openclaw-server-a",
      "tokenHash": "<sha256-token-hash>",
      "enabled": true
    },
    {
      "name": "openclaw-server-b",
      "tokenHash": "<sha256-token-hash>",
      "enabled": true
    }
  ]
}
```

Rules:

- use a different random token for every agent
- keep raw tokens only in each agent's local secret environment
- serve the PMT API over HTTPS; plain HTTP is accepted only for localhost by the MCP adapter
- never commit raw tokens, cookies, session secrets, or Sheet credentials
- rotate a compromised peer token independently
- put the central service behind a firewall, VPN, Cloudflare Access, or an IP allow-list

The REST API prevents identity spoofing: request body `agent_id` must match `X-PMT-Agent` and the bearer-token peer name.

Fine-grained scopes/RBAC and short-lived service tokens are planned hardening items. Until then, peer credentials should be granted only to trusted OpenClaw instances.

## Run the central service

The existing web service now includes PMT routes:

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
cp .env.example .env
# Fill local values; do not commit .env.
set -a; . ./.env; set +a
mcp-transfer-serve
```

Open:

- Web dashboard: `http://127.0.0.1:8787/pmt`
- REST API prefix: `/api/v1/pmt`
- Existing transfer functions remain available.

Runtime PMT database:

```text
$MCP_TRANSFER_BASE_DIR/pmt/pmt.sqlite3
```

Back up the SQLite database together with its `-wal` and `-shm` files, or use SQLite's online backup command while the service is running.

## Configure each OpenClaw MCP client

Install this repository on each OpenClaw server, then configure only local secret references:

```dotenv
MCP_PMT_API_URL=https://pmt.example.com
MCP_PMT_API_TOKEN=<unique-token-for-this-agent>
MCP_PMT_AGENT_ID=openclaw-server-a
```

Generic stdio MCP entry:

```json
{
  "mcpServers": {
    "standalone-pmt": {
      "command": "/opt/mcp-transfer-node/.venv/bin/mcp-pmt-mcp",
      "env": {
        "MCP_PMT_API_URL": "https://pmt.example.com",
        "MCP_PMT_API_TOKEN": "${MCP_PMT_API_TOKEN}",
        "MCP_PMT_AGENT_ID": "openclaw-server-a"
      }
    }
  }
}
```

Prefer the platform's secret store or environment-file support instead of embedding the raw token in JSON.

### MCP tools

Read/context:

- `pmt_get_available_tasks`
- `pmt_get_my_tasks`
- `pmt_get_task`
- `pmt_get_task_context`
- `pmt_get_schedules`

Task writes:

- `pmt_create_task`
- `pmt_register_agent`
- `pmt_claim_task`
- `pmt_task_heartbeat`
- `pmt_start_task`
- `pmt_update_progress`
- `pmt_report_blocker`
- `pmt_submit_for_review`
- `pmt_release_task`

Schedule writes:

- `pmt_create_schedule`
- `pmt_claim_due_schedule`
- `pmt_finish_schedule`

The MCP context pack marks push, MR creation, external status writes, messaging, and deployment as approval gates.

## REST API examples

Headers for every PMT API request:

```text
Authorization: Bearer <agent-token>
X-PMT-Agent: openclaw-server-a
```

Create task:

```bash
curl --fail-with-body https://pmt.example.com/api/v1/pmt/tasks \
  -H "Authorization: Bearer $MCP_PMT_API_TOKEN" \
  -H "X-PMT-Agent: $MCP_PMT_AGENT_ID" \
  -H 'Content-Type: application/json' \
  -d '{
    "title":"Fix Employee access right",
    "project":"HMX",
    "module":"core_hr",
    "menu":"Employee",
    "assignee":"Farhan",
    "priority":"high",
    "target_branch":"Human-Resources",
    "required_checks":["access-matrix","fresh-db-test","prepush-quality"]
  }'
```

Claim task:

```bash
curl --fail-with-body https://pmt.example.com/api/v1/pmt/tasks/PMT-0001/claim \
  -H "Authorization: Bearer $MCP_PMT_API_TOKEN" \
  -H "X-PMT-Agent: $MCP_PMT_AGENT_ID" \
  -H 'Content-Type: application/json' \
  -d '{
    "agent_id":"openclaw-server-a",
    "idempotency_key":"PMT-0001-run-20260712T153000",
    "lease_seconds":1800
  }'
```

Heartbeat:

```bash
curl --fail-with-body https://pmt.example.com/api/v1/pmt/tasks/PMT-0001/heartbeat \
  -H "Authorization: Bearer $MCP_PMT_API_TOKEN" \
  -H "X-PMT-Agent: $MCP_PMT_AGENT_ID" \
  -H 'Content-Type: application/json' \
  -d '{"agent_id":"openclaw-server-a","lease_seconds":1800}'
```

## Google Sheet schedule

The only executable MVP job type is `google_sheet_sync`. It accepts a public Google Sheet CSV export URL and imports matching rows as idempotent PMT tasks.

Default filters follow the current HMX workflow:

- developer/assignee: `Farhan`
- Dev Status: `To-Do`

Create the schedule through the API/MCP:

```json
{
  "name": "Import Farhan To-Do tasks",
  "job_type": "google_sheet_sync",
  "interval_seconds": 300,
  "payload": {
    "csv_url": "https://docs.google.com/spreadsheets/d/<id>/export?format=csv&gid=<gid>",
    "assignee": "Farhan",
    "dev_status": "To-Do",
    "project": "HMX",
    "target_branch": "Human-Resources"
  }
}
```

The importer:

- only accepts HTTPS URLs on `docs.google.com`
- auto-detects a header row within the first 30 CSV rows
- imports only matching task rows
- stores `google_sheet + sheet:<row>` as an idempotent external identity
- does not write back to the Sheet

Run one due schedule:

```bash
MCP_PMT_AGENT_ID=pmt-scheduler mcp-pmt-worker
```

Use one system cron or timer on the **central PMT server** to invoke the worker once per minute. Before installing a system scheduler, inspect and merge with the existing cron/timer configuration. Do not run duplicate central workers unless they use unique IDs; database leases still prevent duplicate ownership.

## HMX recommended agent flow

1. `pmt_get_available_tasks`
2. `pmt_get_task_context`
3. `pmt_claim_task` with a unique idempotency key
4. HMX code search and repository inspection
5. `pmt_start_task`
6. heartbeat during long tests
7. update progress/blocker
8. run module, pre-push, access, pipeline, and UI evidence checks as required
9. `pmt_submit_for_review`
10. wait for explicit approval before push, MR creation, external Sheet update, chat send, or deployment

## Known MVP limitations

- SQLite is appropriate for one central PMT process and moderate agent traffic. Move to PostgreSQL before horizontal API scaling.
- Web login is a single admin password inherited from Transfer Node; use OIDC and RBAC before multi-user/public deployment.
- Peer identities do not yet have fine-grained scopes.
- Schedules are interval-based, not full cron expressions.
- Sheet identity currently includes its row number. A stable source UUID column is recommended before rows are frequently reordered.
- No attachment upload, webhook receiver, GitLab write, Sheet write-back, notification send, or deployment action is implemented.
- Evidence records, approval requests, task dependencies, and sprint analytics remain follow-up modules.

## Next hardening milestones

1. PostgreSQL migrations and `SELECT ... FOR UPDATE SKIP LOCKED`
2. OIDC login, project RBAC, and per-agent scopes
3. stable Sheet source IDs and conflict-aware two-way sync
4. approval-request records for push/MR/message/deploy
5. structured evidence and required-check verdicts
6. GitLab read-only pipeline/MR connector
7. audit export, metrics, backups, and retention policy
8. webhook signatures, rate limits, and reverse-proxy security headers
