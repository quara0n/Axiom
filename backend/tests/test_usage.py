import json
import time

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import Settings
from backend.provider import ProviderError
from backend.runtime import Runtime
from backend.store import Store


def settings(tmp_path, **overrides):
    return Settings(api_key=overrides.pop("api_key", "test-backend-secret"),
                    workspace_root=tmp_path / "workspaces",
                    database=tmp_path / "tasks.sqlite3", **overrides)


def wait_task(client, task_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        value = client.get(f"/api/tasks/{task_id}").json()
        if value["status"] in {"completed", "failed", "cancelled"}:
            return value
        time.sleep(0.01)
    raise AssertionError("Task did not reach terminal state")


USAGE = {"prompt_tokens": 1200, "completion_tokens": 340, "total_tokens": 1540, "cost": 0.0025,
         "prompt_tokens_details": {"cached_tokens": 900},
         "completion_tokens_details": {"reasoning_tokens": 120}}


class UsageProvider:
    """A provider that reports usage, like the real one does."""

    def __init__(self, usage=USAGE):
        self.usage = usage

    async def close(self):
        pass

    async def complete(self, model, messages, tools):
        role = next(role for role in ("Planner", "Coder", "Tester", "Reviewer")
                    if f"You are {role}." in messages[0]["content"])
        if len(messages) == 2:
            name, args = {
                "Planner": ("list_files", {}),
                "Coder": ("write_file", {"path": "main.py", "content": "print('hello')\n"}),
                "Tester": ("validate_file", {"path": "main.py"}),
                "Reviewer": ("read_file", {"path": "main.py"}),
            }[role]
            message = {"content": None, "tool_calls": [{
                "id": role, "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)}}]}
        else:
            body = "Reviewer complete; static checks only."
            message = {"content": json.dumps({"verdict": "approved", "summary": body})
                       if role == "Reviewer" else body}
        message["finish_reason"] = "stop"
        message["usage"] = self.usage
        message["resolved_model"] = model
        message["latency_ms"] = 250
        message["attempts"] = 1
        return message


def test_a_call_records_what_the_provider_reported(tmp_path):
    """Covers AE1: the entry carries the reported figures and the totals sum them."""
    config = settings(tmp_path)
    with TestClient(create_app(config, UsageProvider())) as client:
        task_id = client.post("/api/tasks", json={"task": "Create hello world"}).json()["id"]
        value = wait_task(client, task_id)
    assert value["status"] == "completed", value
    usage = value["usage"]
    assert usage["calls"], "the ledger recorded nothing"
    first = usage["calls"][0]
    assert first["role"] == "Planner"
    assert first["prompt_tokens"] == 1200
    assert first["completion_tokens"] == 340
    assert first["reasoning_tokens"] == 120
    assert first["cached_tokens"] == 900
    assert first["cost"] == 0.0025
    assert first["latency_ms"] == 250
    assert first["finish_reason"] == "stop"
    assert first["resolved_model"] == first["model"]
    assert usage["totals"]["calls"] == len(usage["calls"])
    assert usage["totals"]["prompt_tokens"] == sum(
        call["prompt_tokens"] for call in usage["calls"])
    assert usage["totals"]["cost"] == sum(call["cost"] for call in usage["calls"])
    assert set(usage["by_role"]) == {"Planner", "Coder", "Tester", "Reviewer"}
    assert usage["by_role"]["Planner"]["calls"] == 2


def test_a_silent_provider_is_recorded_as_unknown(tmp_path):
    """Covers AE2: nothing is invented when the provider sends no usage block."""
    config = settings(tmp_path)
    with TestClient(create_app(config, UsageProvider(usage=None))) as client:
        task_id = client.post("/api/tasks", json={"task": "Create hello world"}).json()["id"]
        value = wait_task(client, task_id)
    usage = value["usage"]
    assert usage["calls"][0]["prompt_tokens"] is None
    assert usage["totals"]["calls"] == len(usage["calls"])
    assert usage["totals"]["unknown_calls"] == len(usage["calls"])
    assert "prompt_tokens" not in usage["totals"]


def test_a_failed_call_is_recorded(tmp_path):
    class Broken:
        async def close(self):
            pass

        async def complete(self, model, messages, tools):
            raise ProviderError("Provider unavailable.")

    config = settings(tmp_path)
    with TestClient(create_app(config, Broken())) as client:
        task_id = client.post("/api/tasks", json={"task": "Run"}).json()["id"]
        value = wait_task(client, task_id)
    assert value["status"] == "failed"
    assert value["usage"]["calls"][0]["error"] == "ProviderError"
    assert value["usage"]["calls"][0]["prompt_tokens"] is None


def test_the_ledger_is_bounded_but_the_totals_are_not(tmp_path):
    store = Store(tmp_path / "tasks.sqlite3")
    runtime = Runtime(settings(tmp_path), store, provider=None)
    value = store.create("Long run", "test/model")
    for _ in range(205):
        runtime.record_usage(value, "Coder", "Coder", "test/model",
                             {"usage": USAGE, "latency_ms": 10})
    assert len(value["usage"]["calls"]) == 200
    assert value["usage"]["totals"]["calls"] == 205
    assert value["usage"]["totals"]["prompt_tokens"] == 205 * 1200
    assert value["usage"]["by_role"]["Coder"]["latency_ms"] == 2050


def test_a_worker_call_is_attributed_to_the_worker(tmp_path):
    store = Store(tmp_path / "tasks.sqlite3")
    runtime = Runtime(settings(tmp_path), store, provider=None)
    value = store.create("Delegated", "test/model")
    runtime.record_usage(value, "Coder", "Coder/worker-1", "test/model", {"usage": USAGE})
    entry = value["usage"]["calls"][0]
    assert entry["agent"] == "Coder/worker-1"
    assert entry["role"] == "Coder"
