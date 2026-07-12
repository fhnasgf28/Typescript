from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

TASK_STATUSES = {
    "inbox",
    "todo",
    "claimed",
    "in_progress",
    "ready_for_review",
    "blocked",
    "done",
    "cancelled",
}
TASK_PRIORITIES = {"low", "normal", "high", "urgent"}
EVIDENCE_TYPES = {"commit", "merge_request", "pipeline", "screenshot", "video", "test", "note"}
ADMIN_STATUS_TRANSITIONS = {
    "inbox": {"todo", "cancelled"},
    "todo": {"inbox", "cancelled"},
    "claimed": {"in_progress", "todo", "blocked", "cancelled"},
    "in_progress": {"ready_for_review", "blocked", "todo", "cancelled"},
    "ready_for_review": {"done", "todo", "blocked", "cancelled"},
    "blocked": {"in_progress", "todo", "cancelled"},
    "done": {"todo"},
    "cancelled": {"inbox", "todo"},
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None = None) -> str:
    return (value or utcnow()).isoformat()


@dataclass(frozen=True, slots=True)
class TaskInput:
    title: str
    description: str = ""
    project: str = "HMX"
    module: str = ""
    menu: str = ""
    source: str = "manual"
    external_id: str = ""
    assignee: str = ""
    priority: str = "normal"
    target_branch: str = "Human-Resources"
    acceptance_criteria: tuple[str, ...] = ()
    required_checks: tuple[str, ...] = ()


class PmtStore:
    """Small SQLite-backed PMT store with transactional agent claims."""

    def __init__(self, path: Path):
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS counters (
                    name TEXT PRIMARY KEY,
                    value INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    task_key TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    project TEXT NOT NULL DEFAULT 'HMX',
                    module TEXT NOT NULL DEFAULT '',
                    menu TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'manual',
                    external_id TEXT NOT NULL DEFAULT '',
                    assignee TEXT NOT NULL DEFAULT '',
                    priority TEXT NOT NULL DEFAULT 'normal',
                    status TEXT NOT NULL DEFAULT 'inbox',
                    target_branch TEXT NOT NULL DEFAULT 'Human-Resources',
                    acceptance_criteria TEXT NOT NULL DEFAULT '[]',
                    required_checks TEXT NOT NULL DEFAULT '[]',
                    claimed_by TEXT,
                    lease_expires_at TEXT,
                    progress_note TEXT NOT NULL DEFAULT '',
                    blocker TEXT NOT NULL DEFAULT '',
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_external_source
                    ON tasks(source, external_id) WHERE external_id != '';
                CREATE INDEX IF NOT EXISTS idx_tasks_status_priority
                    ON tasks(status, priority, created_at);
                CREATE INDEX IF NOT EXISTS idx_tasks_claim
                    ON tasks(claimed_by, lease_expires_at);
                CREATE TABLE IF NOT EXISTS task_runs (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES tasks(id),
                    agent_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL,
                    finished_at TEXT
                );
                CREATE TABLE IF NOT EXISTS task_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES tasks(id),
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    payload TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_task_events_task
                    ON task_events(task_id, id);
                CREATE TABLE IF NOT EXISTS task_evidence (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES tasks(id),
                    evidence_type TEXT NOT NULL,
                    label TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_task_evidence_task
                    ON task_evidence(task_id, created_at);
                CREATE TABLE IF NOT EXISTS agents (
                    agent_id TEXT PRIMARY KEY,
                    server_name TEXT NOT NULL,
                    capabilities TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'online',
                    current_task_id TEXT,
                    last_heartbeat_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS schedules (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    job_type TEXT NOT NULL,
                    interval_seconds INTEGER NOT NULL,
                    payload TEXT NOT NULL DEFAULT '{}',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    next_run_at TEXT NOT NULL,
                    locked_by TEXT,
                    lock_expires_at TEXT,
                    last_run_at TEXT,
                    last_status TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS schedule_runs (
                    id TEXT PRIMARY KEY,
                    schedule_id TEXT NOT NULL REFERENCES schedules(id),
                    worker_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    result TEXT NOT NULL DEFAULT '{}'
                );
                """
            )
            db.execute("BEGIN IMMEDIATE")
            try:
                self._ensure_column(db, "tasks", "source_branch", "TEXT NOT NULL DEFAULT ''")
                self._ensure_column(db, "tasks", "commit_ref", "TEXT NOT NULL DEFAULT ''")
                self._ensure_column(db, "tasks", "mr_url", "TEXT NOT NULL DEFAULT ''")
                self._ensure_column(db, "tasks", "pipeline_url", "TEXT NOT NULL DEFAULT ''")
            except Exception:
                db.rollback()
                raise
            else:
                db.commit()

    @staticmethod
    def _ensure_column(db: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=15000")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
            except Exception:
                db.rollback()
                raise
            else:
                db.commit()

    @staticmethod
    def _task(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["acceptance_criteria"] = PmtStore._criteria(
            json.loads(result["acceptance_criteria"])
        )
        result["required_checks"] = json.loads(result["required_checks"])
        return result

    @staticmethod
    def _criteria(items: list[Any]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for index, item in enumerate(items):
            if isinstance(item, str):
                text = item.strip()
                if text:
                    normalized.append({"id": f"criterion-{index + 1}", "text": text, "done": False})
                continue
            if isinstance(item, dict) and str(item.get("text", "")).strip():
                normalized.append(
                    {
                        "id": str(item.get("id") or f"criterion-{index + 1}"),
                        "text": str(item["text"]).strip(),
                        "done": bool(item.get("done", False)),
                    }
                )
        return normalized

    @staticmethod
    def _validated_url(value: str) -> str:
        url = value.strip()
        if not url:
            return ""
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("evidence URL must use http or https")
        return url

    @staticmethod
    def _require_active_owner(row: sqlite3.Row, expected_owner: str | None) -> None:
        if expected_owner is None:
            return
        lease = row["lease_expires_at"]
        if (
            row["claimed_by"] != expected_owner
            or not lease
            or datetime.fromisoformat(lease) <= utcnow()
        ):
            raise PermissionError("task detail writes require the active task owner")

    @staticmethod
    def _schedule(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        result["enabled"] = bool(result["enabled"])
        return result

    def _event(
        self,
        db: sqlite3.Connection,
        task_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        db.execute(
            "INSERT INTO task_events(task_id,event_type,actor,payload,created_at) VALUES(?,?,?,?,?)",
            (task_id, event_type, actor, json.dumps(payload or {}), iso()),
        )

    def _next_key(self, db: sqlite3.Connection, prefix: str = "PMT") -> str:
        db.execute("INSERT INTO counters(name,value) VALUES('task',0) ON CONFLICT(name) DO NOTHING")
        row = db.execute(
            "UPDATE counters SET value=value+1 WHERE name='task' RETURNING value"
        ).fetchone()
        return f"{prefix}-{int(row['value']):04d}"

    def create_task(self, data: TaskInput, actor: str = "human") -> dict[str, Any]:
        title = data.title.strip()
        if not title:
            raise ValueError("title is required")
        if data.priority not in TASK_PRIORITIES:
            raise ValueError(f"invalid priority: {data.priority}")
        task_id = f"task_{uuid.uuid4().hex}"
        now = iso()
        with self._transaction() as db:
            if data.external_id:
                existing = db.execute(
                    "SELECT * FROM tasks WHERE source=? AND external_id=?",
                    (data.source, data.external_id),
                ).fetchone()
                if existing is not None:
                    return self._task(existing)
            task_key = self._next_key(db)
            try:
                db.execute(
                    """
                    INSERT INTO tasks(
                        id,task_key,title,description,project,module,menu,source,external_id,
                        assignee,priority,status,target_branch,acceptance_criteria,required_checks,
                        created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        task_id,
                        task_key,
                        title,
                        data.description.strip(),
                        data.project.strip() or "HMX",
                        data.module.strip(),
                        data.menu.strip(),
                        data.source.strip() or "manual",
                        data.external_id.strip(),
                        data.assignee.strip(),
                        data.priority,
                        "todo",
                        data.target_branch.strip() or "Human-Resources",
                        json.dumps(
                            [
                                {
                                    "id": f"criterion_{uuid.uuid4().hex}",
                                    "text": text.strip(),
                                    "done": False,
                                }
                                for text in data.acceptance_criteria
                                if text.strip()
                            ]
                        ),
                        json.dumps(list(data.required_checks)),
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                if data.external_id:
                    existing = db.execute(
                        "SELECT * FROM tasks WHERE source=? AND external_id=?",
                        (data.source, data.external_id),
                    ).fetchone()
                    if existing is not None:
                        return self._task(existing)
                raise ValueError("task key or external source already exists") from exc
            self._event(db, task_id, "task.created", actor, {"task_key": task_key})
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            return self._task(row)

    def list_tasks(
        self,
        *,
        status: str | None = None,
        assignee: str | None = None,
        claimed_by: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if status:
            if status not in TASK_STATUSES:
                raise ValueError(f"invalid status: {status}")
            clauses.append("status=?")
            values.append(status)
        if assignee:
            clauses.append("assignee=?")
            values.append(assignee)
        if claimed_by:
            clauses.append("claimed_by=?")
            values.append(claimed_by)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(max(1, min(limit, 200)))
        with self._connect() as db:
            rows = db.execute(
                f"""SELECT * FROM tasks {where}
                ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1
                         WHEN 'normal' THEN 2 ELSE 3 END, created_at LIMIT ?""",
                values,
            ).fetchall()
        return [self._task(row) for row in rows]

    def get_task(self, task_ref: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM tasks WHERE id=? OR task_key=?", (task_ref, task_ref)
            ).fetchone()
        return self._task(row) if row else None

    def get_external_task(self, source: str, external_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM tasks WHERE source=? AND external_id=?", (source, external_id)
            ).fetchone()
        return self._task(row) if row else None

    def task_events(self, task_ref: str, limit: int = 100) -> list[dict[str, Any]]:
        task = self.get_task(task_ref)
        if task is None:
            raise KeyError(task_ref)
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM task_events WHERE task_id=? ORDER BY id DESC LIMIT ?",
                (task["id"], max(1, min(limit, 500))),
            ).fetchall()
        result = []
        for row in rows:
            event = dict(row)
            event["payload"] = json.loads(event["payload"])
            result.append(event)
        return result

    def update_task(
        self,
        task_ref: str,
        *,
        actor: str,
        title: str,
        description: str = "",
        project: str = "HMX",
        module: str = "",
        menu: str = "",
        assignee: str = "",
        priority: str = "normal",
        required_checks: list[str] | tuple[str, ...] | None = None,
        target_branch: str = "Human-Resources",
        source_branch: str = "",
        commit_ref: str = "",
        mr_url: str = "",
        pipeline_url: str = "",
        expected_owner: str | None = None,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        if not title.strip():
            raise ValueError("title is required")
        if priority not in TASK_PRIORITIES:
            raise ValueError(f"invalid priority: {priority}")
        mr_url = self._validated_url(mr_url)
        pipeline_url = self._validated_url(pipeline_url)
        with self._transaction() as db:
            row = db.execute(
                "SELECT * FROM tasks WHERE id=? OR task_key=?", (task_ref, task_ref)
            ).fetchone()
            if row is None:
                raise KeyError(task_ref)
            self._require_active_owner(row, expected_owner)
            if expected_version is not None and row["version"] != expected_version:
                raise PermissionError("task changed since it was loaded; refresh and retry")
            values = {
                "title": title.strip(),
                "description": description.strip(),
                "project": project.strip() or "HMX",
                "module": module.strip(),
                "menu": menu.strip(),
                "assignee": assignee.strip(),
                "priority": priority,
                "required_checks": (
                    row["required_checks"]
                    if required_checks is None
                    else json.dumps([check.strip() for check in required_checks if check.strip()])
                ),
                "target_branch": target_branch.strip() or "Human-Resources",
                "source_branch": source_branch.strip(),
                "commit_ref": commit_ref.strip(),
                "mr_url": mr_url,
                "pipeline_url": pipeline_url,
            }
            changed = {key: value for key, value in values.items() if value != row[key]}
            if changed:
                db.execute(
                    """UPDATE tasks SET title=?,description=?,project=?,module=?,menu=?,assignee=?,
                        priority=?,required_checks=?,target_branch=?,source_branch=?,commit_ref=?,mr_url=?,pipeline_url=?,
                        version=version+1,updated_at=? WHERE id=?""",
                    (*values.values(), iso(), row["id"]),
                )
                self._event(db, row["id"], "task.updated", actor, {"changed": changed})
            updated = db.execute("SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone()
            return self._task(updated)

    def add_acceptance_criterion(
        self, task_ref: str, text: str, actor: str, expected_owner: str | None = None
    ) -> dict[str, Any]:
        if not text.strip():
            raise ValueError("criterion text is required")
        with self._transaction() as db:
            row = db.execute(
                "SELECT * FROM tasks WHERE id=? OR task_key=?", (task_ref, task_ref)
            ).fetchone()
            if row is None:
                raise KeyError(task_ref)
            self._require_active_owner(row, expected_owner)
            criteria = self._criteria(json.loads(row["acceptance_criteria"]))
            criterion = {
                "id": f"criterion_{uuid.uuid4().hex}",
                "text": text.strip(),
                "done": False,
            }
            criteria.append(criterion)
            db.execute(
                """UPDATE tasks SET acceptance_criteria=?,version=version+1,updated_at=?
                    WHERE id=?""",
                (json.dumps(criteria), iso(), row["id"]),
            )
            self._event(db, row["id"], "criterion.added", actor, criterion)
            updated = db.execute("SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone()
            return self._task(updated)

    def toggle_acceptance_criterion(
        self,
        task_ref: str,
        criterion_id: str,
        actor: str,
        expected_owner: str | None = None,
    ) -> dict[str, Any]:
        with self._transaction() as db:
            row = db.execute(
                "SELECT * FROM tasks WHERE id=? OR task_key=?", (task_ref, task_ref)
            ).fetchone()
            if row is None:
                raise KeyError(task_ref)
            self._require_active_owner(row, expected_owner)
            criteria = self._criteria(json.loads(row["acceptance_criteria"]))
            criterion = next((item for item in criteria if item["id"] == criterion_id), None)
            if criterion is None:
                raise KeyError(criterion_id)
            criterion["done"] = not criterion["done"]
            db.execute(
                """UPDATE tasks SET acceptance_criteria=?,version=version+1,updated_at=?
                    WHERE id=?""",
                (json.dumps(criteria), iso(), row["id"]),
            )
            self._event(db, row["id"], "criterion.toggled", actor, criterion)
            updated = db.execute("SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone()
            return self._task(updated)

    def admin_transition_task(
        self, task_ref: str, target_status: str, actor: str, note: str = ""
    ) -> dict[str, Any]:
        if target_status not in TASK_STATUSES:
            raise ValueError(f"invalid status: {target_status}")
        with self._transaction() as db:
            row = db.execute(
                "SELECT * FROM tasks WHERE id=? OR task_key=?", (task_ref, task_ref)
            ).fetchone()
            if row is None:
                raise KeyError(task_ref)
            if target_status == row["status"]:
                return self._task(row)
            allowed = ADMIN_STATUS_TRANSITIONS.get(row["status"], set())
            if target_status not in allowed:
                raise ValueError(f"invalid manual transition: {row['status']} -> {target_status}")
            if target_status in {"claimed", "in_progress"} and not row["claimed_by"]:
                raise ValueError(f"{target_status} status requires an agent owner")
            now = iso()
            releases = target_status in {
                "inbox",
                "todo",
                "ready_for_review",
                "done",
                "cancelled",
            }
            claimed_by = None if releases else row["claimed_by"]
            lease = None if releases else row["lease_expires_at"]
            db.execute(
                """UPDATE tasks SET status=?,claimed_by=?,lease_expires_at=?,progress_note=?,
                    blocker=?,version=version+1,updated_at=? WHERE id=?""",
                (
                    target_status,
                    claimed_by,
                    lease,
                    note.strip(),
                    note.strip() if target_status == "blocked" else "",
                    now,
                    row["id"],
                ),
            )
            if releases and row["claimed_by"]:
                db.execute(
                    """UPDATE task_runs SET status=?,finished_at=? WHERE task_id=?
                        AND finished_at IS NULL""",
                    (target_status, now, row["id"]),
                )
                db.execute(
                    """UPDATE agents SET current_task_id=NULL,status='online',updated_at=?
                        WHERE agent_id=?""",
                    (now, row["claimed_by"]),
                )
            self._event(
                db,
                row["id"],
                f"task.{target_status}",
                actor,
                {"note": note, "manual": True},
            )
            updated = db.execute("SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone()
            return self._task(updated)

    def add_evidence(
        self,
        task_ref: str,
        *,
        evidence_type: str,
        label: str,
        url: str,
        note: str,
        actor: str,
        expected_owner: str | None = None,
    ) -> dict[str, Any]:
        if evidence_type not in EVIDENCE_TYPES:
            raise ValueError(f"invalid evidence type: {evidence_type}")
        url = self._validated_url(url)
        if not label.strip() and not url and not note.strip():
            raise ValueError("evidence requires a label, URL, or note")
        with self._transaction() as db:
            row = db.execute(
                "SELECT * FROM tasks WHERE id=? OR task_key=?", (task_ref, task_ref)
            ).fetchone()
            if row is None:
                raise KeyError(task_ref)
            self._require_active_owner(row, expected_owner)
            evidence_id = f"evidence_{uuid.uuid4().hex}"
            created_at = iso()
            db.execute(
                """INSERT INTO task_evidence(
                    id,task_id,evidence_type,label,url,note,actor,created_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    evidence_id,
                    row["id"],
                    evidence_type,
                    label.strip(),
                    url,
                    note.strip(),
                    actor,
                    created_at,
                ),
            )
            payload = {
                "id": evidence_id,
                "evidence_type": evidence_type,
                "label": label.strip(),
                "url": url,
                "note": note.strip(),
            }
            self._event(db, row["id"], "evidence.added", actor, payload)
            return {**payload, "actor": actor, "created_at": created_at}

    def list_evidence(self, task_ref: str) -> list[dict[str, Any]]:
        task = self.get_task(task_ref)
        if task is None:
            raise KeyError(task_ref)
        with self._connect() as db:
            rows = db.execute(
                """SELECT id,evidence_type,label,url,note,actor,created_at
                    FROM task_evidence WHERE task_id=? ORDER BY created_at DESC""",
                (task["id"],),
            ).fetchall()
        return [dict(row) for row in rows]

    def register_agent(
        self, agent_id: str, server_name: str, capabilities: list[str] | None = None
    ) -> dict[str, Any]:
        if not agent_id.strip() or not server_name.strip():
            raise ValueError("agent_id and server_name are required")
        now = iso()
        with self._transaction() as db:
            db.execute(
                """
                INSERT INTO agents(agent_id,server_name,capabilities,last_heartbeat_at,created_at,updated_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    server_name=excluded.server_name,
                    capabilities=excluded.capabilities,
                    status='online',
                    last_heartbeat_at=excluded.last_heartbeat_at,
                    updated_at=excluded.updated_at
                """,
                (agent_id, server_name, json.dumps(capabilities or []), now, now, now),
            )
            row = db.execute("SELECT * FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
        result = dict(row)
        result["capabilities"] = json.loads(result["capabilities"])
        return result

    def claim_task(
        self,
        task_ref: str,
        agent_id: str,
        idempotency_key: str,
        lease_seconds: int = 1800,
    ) -> dict[str, Any]:
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        lease_seconds = max(60, min(lease_seconds, 7200))
        now = utcnow()
        expires = now + timedelta(seconds=lease_seconds)
        with self._transaction() as db:
            prior = db.execute(
                "SELECT task_id,agent_id FROM task_runs WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if prior:
                row = db.execute("SELECT * FROM tasks WHERE id=?", (prior["task_id"],)).fetchone()
                if prior["agent_id"] != agent_id or task_ref not in {row["id"], row["task_key"]}:
                    raise PermissionError("idempotency key belongs to another claim")
                return self._task(row)
            row = db.execute(
                "SELECT * FROM tasks WHERE id=? OR task_key=?", (task_ref, task_ref)
            ).fetchone()
            if row is None:
                raise KeyError(task_ref)
            task = self._task(row)
            lease_expired = bool(
                task["lease_expires_at"] and task["lease_expires_at"] <= now.isoformat()
            )
            if task["claimed_by"] and task["claimed_by"] != agent_id and not lease_expired:
                raise PermissionError(f"task already claimed by {task['claimed_by']}")
            if task["status"] in {"done", "cancelled", "ready_for_review"}:
                raise ValueError(f"task cannot be claimed from status {task['status']}")
            run_id = f"run_{uuid.uuid4().hex}"
            db.execute(
                """UPDATE tasks SET status='claimed',claimed_by=?,lease_expires_at=?,
                    version=version+1,updated_at=? WHERE id=?""",
                (agent_id, expires.isoformat(), now.isoformat(), task["id"]),
            )
            db.execute(
                """INSERT INTO task_runs(id,task_id,agent_id,idempotency_key,status,started_at,heartbeat_at)
                    VALUES(?,?,?,?,?,?,?)""",
                (
                    run_id,
                    task["id"],
                    agent_id,
                    idempotency_key,
                    "claimed",
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            db.execute(
                """UPDATE agents SET current_task_id=?,status='busy',last_heartbeat_at=?,updated_at=?
                    WHERE agent_id=?""",
                (task["id"], now.isoformat(), now.isoformat(), agent_id),
            )
            self._event(
                db,
                task["id"],
                "task.claimed",
                agent_id,
                {"run_id": run_id, "lease_expires_at": expires.isoformat()},
            )
            updated = db.execute("SELECT * FROM tasks WHERE id=?", (task["id"],)).fetchone()
            return self._task(updated)

    def heartbeat(self, task_ref: str, agent_id: str, lease_seconds: int = 1800) -> dict[str, Any]:
        lease_seconds = max(60, min(lease_seconds, 7200))
        now = utcnow()
        expires = now + timedelta(seconds=lease_seconds)
        with self._transaction() as db:
            row = db.execute(
                "SELECT * FROM tasks WHERE id=? OR task_key=?", (task_ref, task_ref)
            ).fetchone()
            if row is None:
                raise KeyError(task_ref)
            if row["claimed_by"] != agent_id:
                raise PermissionError("agent does not own this task")
            db.execute(
                "UPDATE tasks SET lease_expires_at=?,updated_at=? WHERE id=?",
                (expires.isoformat(), now.isoformat(), row["id"]),
            )
            db.execute(
                """UPDATE task_runs SET heartbeat_at=? WHERE task_id=? AND agent_id=?
                    AND finished_at IS NULL""",
                (now.isoformat(), row["id"], agent_id),
            )
            db.execute(
                "UPDATE agents SET last_heartbeat_at=?,updated_at=? WHERE agent_id=?",
                (now.isoformat(), now.isoformat(), agent_id),
            )
            updated = db.execute("SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone()
            return self._task(updated)

    def transition_task(
        self,
        task_ref: str,
        agent_id: str,
        target_status: str,
        *,
        note: str = "",
        blocker: str = "",
    ) -> dict[str, Any]:
        if target_status not in TASK_STATUSES:
            raise ValueError(f"invalid status: {target_status}")
        with self._transaction() as db:
            row = db.execute(
                "SELECT * FROM tasks WHERE id=? OR task_key=?", (task_ref, task_ref)
            ).fetchone()
            if row is None:
                raise KeyError(task_ref)
            if row["claimed_by"] != agent_id:
                raise PermissionError("agent does not own this task")
            now = iso()
            releases = target_status in {"ready_for_review", "done", "cancelled", "todo"}
            db.execute(
                """UPDATE tasks SET status=?,progress_note=?,blocker=?,claimed_by=?,
                    lease_expires_at=?,version=version+1,updated_at=? WHERE id=?""",
                (
                    target_status,
                    note.strip(),
                    blocker.strip(),
                    None if releases else agent_id,
                    None if releases else row["lease_expires_at"],
                    now,
                    row["id"],
                ),
            )
            if releases:
                db.execute(
                    """UPDATE task_runs SET status=?,finished_at=? WHERE task_id=?
                        AND agent_id=? AND finished_at IS NULL""",
                    (target_status, now, row["id"], agent_id),
                )
                db.execute(
                    """UPDATE agents SET current_task_id=NULL,status='online',updated_at=?
                        WHERE agent_id=?""",
                    (now, agent_id),
                )
            self._event(
                db,
                row["id"],
                f"task.{target_status}",
                agent_id,
                {"note": note, "blocker": blocker},
            )
            updated = db.execute("SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone()
            return self._task(updated)

    def create_schedule(
        self,
        name: str,
        job_type: str,
        interval_seconds: int,
        payload: dict[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        if not name.strip() or not job_type.strip():
            raise ValueError("name and job_type are required")
        interval_seconds = max(60, min(interval_seconds, 31 * 24 * 3600))
        schedule_id = f"schedule_{uuid.uuid4().hex}"
        now = utcnow()
        with self._transaction() as db:
            db.execute(
                """INSERT INTO schedules(
                    id,name,job_type,interval_seconds,payload,next_run_at,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    schedule_id,
                    name.strip(),
                    job_type.strip(),
                    interval_seconds,
                    json.dumps(payload),
                    (now + timedelta(seconds=interval_seconds)).isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            row = db.execute("SELECT * FROM schedules WHERE id=?", (schedule_id,)).fetchone()
        return self._schedule(row)

    def list_schedules(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM schedules ORDER BY created_at").fetchall()
        return [self._schedule(row) for row in rows]

    def claim_due_schedule(self, worker_id: str, lease_seconds: int = 300) -> dict[str, Any] | None:
        now = utcnow()
        with self._transaction() as db:
            row = db.execute(
                """SELECT * FROM schedules WHERE enabled=1 AND next_run_at<=?
                    AND (locked_by IS NULL OR lock_expires_at<=?) ORDER BY next_run_at LIMIT 1""",
                (now.isoformat(), now.isoformat()),
            ).fetchone()
            if row is None:
                return None
            run_id = f"schedule_run_{uuid.uuid4().hex}"
            db.execute(
                "UPDATE schedules SET locked_by=?,lock_expires_at=?,updated_at=? WHERE id=?",
                (
                    worker_id,
                    (now + timedelta(seconds=max(60, lease_seconds))).isoformat(),
                    now.isoformat(),
                    row["id"],
                ),
            )
            db.execute(
                """INSERT INTO schedule_runs(id,schedule_id,worker_id,status,started_at)
                    VALUES(?,?,?,?,?)""",
                (run_id, row["id"], worker_id, "running", now.isoformat()),
            )
            claimed = self._schedule(
                db.execute("SELECT * FROM schedules WHERE id=?", (row["id"],)).fetchone()
            )
            claimed["run_id"] = run_id
            return claimed

    def finish_schedule_run(
        self, schedule_id: str, run_id: str, worker_id: str, status: str, result: dict[str, Any]
    ) -> dict[str, Any]:
        if status not in {"succeeded", "failed", "skipped"}:
            raise ValueError("invalid schedule run status")
        now = utcnow()
        with self._transaction() as db:
            row = db.execute("SELECT * FROM schedules WHERE id=?", (schedule_id,)).fetchone()
            if row is None:
                raise KeyError(schedule_id)
            if row["locked_by"] != worker_id:
                raise PermissionError("worker does not own this schedule lease")
            db.execute(
                """UPDATE schedule_runs SET status=?,finished_at=?,result=?
                    WHERE id=? AND schedule_id=? AND worker_id=?""",
                (status, now.isoformat(), json.dumps(result), run_id, schedule_id, worker_id),
            )
            db.execute(
                """UPDATE schedules SET locked_by=NULL,lock_expires_at=NULL,last_run_at=?,
                    last_status=?,next_run_at=?,updated_at=? WHERE id=?""",
                (
                    now.isoformat(),
                    status,
                    (now + timedelta(seconds=row["interval_seconds"])).isoformat(),
                    now.isoformat(),
                    schedule_id,
                ),
            )
            updated = db.execute("SELECT * FROM schedules WHERE id=?", (schedule_id,)).fetchone()
            return self._schedule(updated)
