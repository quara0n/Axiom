import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROLES = ("Planner", "Coder", "Tester", "Reviewer")
TERMINAL = {"completed", "failed", "cancelled"}
# Threads are filed under a project. It is a label the operator reads in the
# sidebar, never a path, a key or an identifier anything else depends on.
DEFAULT_PROJECT = "Axiom"


def project_name(value):
    if not isinstance(value, str):
        return DEFAULT_PROJECT
    name = " ".join(value.split())[:60].strip()
    return name or DEFAULT_PROJECT


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    """Single-process runtime with transactional, durable SQLite snapshots."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, data TEXT NOT NULL)")

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def create(self, task: str, model: str, models=None, *, persist=True, project=None):
        chosen = {role: models[role] for role in ROLES if models and models.get(role)}
        value = {
            "id": uuid4().hex, "task": task, "model": model, "models": chosen,
            "project": project_name(project), "status": "queued",
            "created_at": now(), "updated_at": now(), "summary": "", "error": None,
            "agents": [{"name": role, "status": "pending", "assignment": task, "result": "",
                        "model": chosen.get(role, model)}
                       for role in ROLES], "events": [],
        }
        if persist:
            self.save(value)
        return value

    def save(self, value):
        value["updated_at"] = now()
        with self.connect() as db:
            db.execute("INSERT INTO tasks (id, data) VALUES (?, ?) "
                       "ON CONFLICT(id) DO UPDATE SET data = excluded.data",
                       (value["id"], json.dumps(value, ensure_ascii=False)))

    def get(self, task_id):
        with self.connect() as db:
            row = db.execute("SELECT data FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def list(self):
        with self.connect() as db:
            rows = db.execute("SELECT data FROM tasks ORDER BY rowid DESC LIMIT 100").fetchall()
        return [json.loads(row[0]) for row in rows]

    def delete(self, task_id):
        with self.connect() as db:
            cursor = db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        return cursor.rowcount > 0

    def recover(self):
        """A restart no longer throws a run away: the workspace is still there.

        Returns the ids that were interrupted, so a caller that wants to can start
        continuations for them instead of waiting for the operator.
        """
        interrupted = []
        with self.connect() as db:
            rows = db.execute("SELECT data FROM tasks").fetchall()
        for row in rows:
            value = json.loads(row[0])
            if value["status"] not in TERMINAL:
                value["status"] = "interrupted"
                value["error"] = ("The backend restarted while this task was running. Resume it "
                                  "to continue from the files it already wrote.")
                for agent in value["agents"]:
                    if agent["status"] in {"running", "pending"}:
                        agent["status"] = "interrupted"
                self.save(value)
                interrupted.append(value["id"])
        return interrupted
