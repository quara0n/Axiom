"""A slow model call is visible while it waits, bounded, and recorded when it is cut."""

import asyncio
import time

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import Settings
from backend.runtime import Runtime
from backend.store import Store


class SlowProvider:
    """Never answers in time, so the call budget is what has to end the wait."""

    def __init__(self, delay=10.0):
        self.delay = delay

    async def close(self):
        pass

    async def models(self):
        return []

    async def complete(self, model, messages, tools):
        await asyncio.sleep(self.delay)
        return {"content": "done"}


class FastProvider:
    """Answers slowly enough that the waiting line is written first, and acts once."""

    def __init__(self, delay=0.15):
        self.delay = delay
        self.calls = 0

    async def close(self):
        pass

    async def models(self):
        return []

    async def complete(self, model, messages, tools):
        await asyncio.sleep(self.delay)
        self.calls += 1
        if self.calls == 1:
            return {"content": None, "tool_calls": [
                {"id": "call-1", "type": "function",
                 "function": {"name": "list_files", "arguments": "{}"}}]}
        return {"content": "Nothing to do."}


def settings(tmp_path, **overrides):
    return Settings(api_key="test", workspace_root=tmp_path / "workspaces",
                    database=tmp_path / "tasks.sqlite3", **overrides)


def wait_task(client, task_id):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        value = client.get(f"/api/tasks/{task_id}").json()
        if value["status"] in {"completed", "failed", "cancelled"}:
            return value
        time.sleep(0.01)
    raise AssertionError("task did not reach a terminal state")


def test_one_budget_covers_a_whole_call_including_its_retries(tmp_path):
    config = settings(tmp_path, model_call_timeout=1.0, planner_model_call_timeout=1.0)
    with TestClient(create_app(config, SlowProvider(10))) as client:
        task_id = client.post("/api/tasks", json={"task": "Run"}).json()["id"]
        value = wait_task(client, task_id)
    assert value["status"] == "failed"
    assert "call budget" in value["error"]
    # The cut-off call is in the ledger as unknown consumption, not missing from it.
    assert value["usage"]["totals"]["unknown_calls"] >= 1
    assert any(entry["error"] == "TimeoutError" for entry in value["usage"]["calls"])
    assert any("was cut off" in event["text"] for event in value["events"])


def test_the_wait_is_visible_before_the_model_answers(tmp_path):
    config = settings(tmp_path)
    with TestClient(create_app(config, FastProvider(0.15))) as client:
        task_id = client.post("/api/tasks", json={"task": "Run"}).json()["id"]
        value = wait_task(client, task_id)
    texts = [event["text"] for event in value["events"]]
    waiting = [text for text in texts if "is waiting for" in text]
    assert waiting, texts
    # The wait line is written before the first tool line, so the dashboard is never
    # left showing the previous call while a slow model holds the turn.
    assert waiting[0].startswith("Planner is waiting for")
    first_tool = next(index for index, text in enumerate(texts) if text.startswith("Tool "))
    assert texts.index(waiting[0]) < first_tool
    assert "(budget" in waiting[0]


def test_the_planner_gets_its_own_shorter_budget(tmp_path):
    config = settings(tmp_path, model_call_timeout=240, planner_model_call_timeout=42)
    runtime = Runtime(config, Store(config.database), SlowProvider(0))
    assert runtime.call_budget("Planner") == 42
    assert runtime.call_budget("Coder") == 240
    unlimited = settings(tmp_path, model_call_timeout=0, planner_model_call_timeout=0)
    runtime = Runtime(unlimited, Store(unlimited.database), SlowProvider(0))
    assert runtime.call_budget("Planner") == 0
    assert runtime.call_budget("Reviewer") == 0
