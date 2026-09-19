"""A slow model call is visible while it waits, bounded, and recorded when it is cut."""

import asyncio
import time

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import Settings
from backend import jev_shadow as shadow
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


class BlockingProvider:
    """Acts once, then holds the next call open so a cancel lands mid-flight."""

    def __init__(self):
        self.calls = 0

    async def close(self):
        pass

    async def models(self):
        return []

    async def complete(self, model, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return {"content": None, "tool_calls": [
                {"id": "call-1", "type": "function",
                 "function": {"name": "list_files", "arguments": "{}"}}]}
        await asyncio.sleep(30)
        return {"content": "never reached"}


class EmptyThenActProvider:
    """Answers with nothing, then acts: an empty reply is not a decision."""

    def __init__(self):
        self.calls = 0

    async def close(self):
        pass

    async def models(self):
        return []

    async def complete(self, model, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return {"content": "   "}
        if self.calls == 2:
            return {"content": None, "tool_calls": [
                {"id": "call-1", "type": "function",
                 "function": {"name": "list_files", "arguments": "{}"}}]}
        return {"content": "Report: nothing left to do."}


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


def test_a_slow_call_keeps_saying_that_it_is_still_waiting(tmp_path):
    config = settings(tmp_path, model_call_timeout=1.2, planner_model_call_timeout=1.2,
                      wait_update_seconds=0.2)
    with TestClient(create_app(config, SlowProvider(10))) as client:
        task_id = client.post("/api/tasks", json={"task": "Run"}).json()["id"]
        value = wait_task(client, task_id)
    texts = [event["text"] for event in value["events"]]
    waiting = [text for text in texts if "is waiting for" in text]
    still = [text for text in texts if "is still waiting" in text]
    assert waiting and still, texts
    # The update carries the elapsed seconds and the budget, so a live run is
    # distinguishable from a dead one without reading the database.
    assert "is still waiting for" in still[0] and "s of" in still[0]
    assert still[0] in texts[texts.index(waiting[0]):]


def test_a_cancelled_task_records_the_call_that_was_in_flight(tmp_path):
    config = settings(tmp_path)
    with TestClient(create_app(config, BlockingProvider())) as client:
        task_id = client.post("/api/tasks", json={"task": "Run"}).json()["id"]
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if client.get(f"/api/tasks/{task_id}").json()["model_calls"] >= 2:
                break
            time.sleep(0.02)
        client.post(f"/api/tasks/{task_id}/cancel")
        value = wait_task(client, task_id)
    assert value["status"] == "cancelled"
    # Both calls are in the ledger: the one that finished and the one that was cut off.
    assert len(value["usage"]["calls"]) == 2
    assert any(entry["error"] == "CancelledError" for entry in value["usage"]["calls"])
    assert value["usage"]["totals"]["unknown_calls"] >= 1


def test_an_empty_answer_is_not_recorded_as_a_decision(tmp_path, monkeypatch):
    """The workflow records what it actually did, never what the model merely returned."""
    recorded = []
    monkeypatch.setattr("backend.runtime.note_next_action",
                        lambda value, action: recorded.append(action))
    config = settings(tmp_path)
    with TestClient(create_app(config, EmptyThenActProvider())) as client:
        task_id = client.post("/api/tasks", json={"task": "Run"}).json()["id"]
        wait_task(client, task_id)
    assert recorded, recorded
    # The empty answer produced no action at all, so the first recorded action is the
    # tool call that actually ran - not a fabricated finish.
    assert recorded[0] == shadow.CONTINUE
    assert shadow.FINISH in recorded
