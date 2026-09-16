import asyncio
import json
import time

from fastapi.testclient import TestClient
import httpx
import pytest

from backend.app import create_app
from backend.config import Settings
from backend.provider import OpenRouter, ProviderError

from backend.tests.test_backend import MockProvider

BODY = {"model": "test/model", "id": "gen-1",
        "choices": [{"message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2}}


class FakeClient:
    """A stand-in transport so a rate limit can be provoked without the network."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.posts = 0

    async def post(self, url, json=None):
        self.posts += 1
        status, body, headers = self.responses.pop(0) if self.responses else (500, {}, {})
        if isinstance(body, Exception):
            raise body
        return httpx.Response(status, json=body, headers=headers,
                              request=httpx.Request("POST", "https://openrouter.ai/api/v1/x"))

    async def aclose(self):
        pass


def provider_with(responses, **overrides):
    provider = OpenRouter("test-key", max_attempts=overrides.pop("max_attempts", 3),
                          retry_base=overrides.pop("retry_base", 0.01), **overrides)
    provider.client = FakeClient(responses)
    return provider


def settings(tmp_path, **overrides):
    return Settings(api_key=overrides.pop("api_key", "test-backend-secret"),
                    workspace_root=tmp_path / "workspaces",
                    database=tmp_path / "tasks.sqlite3", **overrides)


def wait_task(client, task_id, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = client.get(f"/api/tasks/{task_id}").json()
        if value["status"] in {"completed", "failed", "cancelled", "interrupted"}:
            return value
        time.sleep(0.02)
    raise AssertionError("Task did not reach a terminal state")


def test_a_rate_limit_is_retried_and_the_second_attempt_succeeds():
    """Covers AE9: one 429 is not the end of the task."""
    provider = provider_with([(429, {}, {}), (200, BODY, {})])
    message = asyncio.run(provider.complete("test/model", [{"role": "user", "content": "hi"}], []))
    assert message["content"] == "hello"
    assert message["attempts"] == 2
    assert provider.client.posts == 2


def test_a_persistent_rate_limit_stops_after_the_bounded_attempts():
    provider = provider_with([(429, {}, {})] * 5, max_attempts=3)
    with pytest.raises(ProviderError) as error:
        asyncio.run(provider.complete("test/model", [{"role": "user", "content": "hi"}], []))
    assert "429" in str(error.value)
    assert provider.client.posts == 3


def test_a_rejected_key_is_not_retried():
    provider = provider_with([(401, {}, {}), (200, BODY, {})])
    with pytest.raises(ProviderError):
        asyncio.run(provider.complete("test/model", [{"role": "user", "content": "hi"}], []))
    assert provider.client.posts == 1


def test_an_empty_balance_is_not_retried():
    provider = provider_with([(402, {}, {}), (200, BODY, {})])
    with pytest.raises(ProviderError):
        asyncio.run(provider.complete("test/model", [{"role": "user", "content": "hi"}], []))
    assert provider.client.posts == 1


def test_a_transport_failure_is_retried_then_reported():
    provider = provider_with([(0, httpx.ConnectError("no route"), {})] * 5, max_attempts=3)
    with pytest.raises(ProviderError) as error:
        asyncio.run(provider.complete("test/model", [{"role": "user", "content": "hi"}], []))
    assert "3 attempt" in str(error.value)
    assert provider.client.posts == 3


def test_an_interrupted_task_can_be_resumed_from_its_workspace(tmp_path):
    """Covers AE10: a restart leaves the run recoverable, not lost."""
    config = settings(tmp_path)
    with TestClient(create_app(config, MockProvider())) as client:
        source = client.app.state.store.create("Half-finished game", "test/model")
        source["status"] = "running"
        client.app.state.store.save(source)
        workspace = config.workspace_root / source["id"]
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "main.py").write_text("print('half')\n", encoding="utf-8")

        assert client.app.state.store.recover() == [source["id"]]
        interrupted = client.get(f"/api/tasks/{source['id']}").json()
        assert interrupted["status"] == "interrupted"
        assert "Resume it" in interrupted["error"]

        response = client.post(f"/api/tasks/{source['id']}/resume")
        assert response.status_code == 202
        continued = response.json()
        assert continued["continue_from"] == source["id"]
        assert continued["files"] == ["main.py"]
        assert continued["continuation_depth"] == 1
        assert wait_task(client, continued["id"])["status"] in {"completed", "failed"}


def test_resuming_a_running_task_is_refused(tmp_path):
    config = settings(tmp_path)
    with TestClient(create_app(config, MockProvider())) as client:
        source = client.app.state.store.create("Still running", "test/model")
        source["status"] = "running"
        client.app.state.store.save(source)
        assert client.post(f"/api/tasks/{source['id']}/resume").status_code == 409


def test_no_recovery_starts_without_the_operator(tmp_path):
    """Covers AE10: the shipped default spends nothing on its own."""
    config = settings(tmp_path)
    with TestClient(create_app(config, MockProvider())) as client:
        source = client.app.state.store.create("Interrupted", "test/model")
        source["status"] = "running"
        client.app.state.store.save(source)
        (config.workspace_root / source["id"]).mkdir(parents=True, exist_ok=True)
    with TestClient(create_app(settings(tmp_path), MockProvider())) as client:
        tasks = client.get("/api/tasks").json()["tasks"]
        assert [task["id"] for task in tasks] == [source["id"]]
        assert tasks[0]["status"] == "interrupted"


def test_auto_resume_starts_a_continuation_when_enabled(tmp_path):
    config = settings(tmp_path)
    with TestClient(create_app(config, MockProvider())) as client:
        source = client.app.state.store.create("Interrupted", "test/model")
        source["status"] = "running"
        client.app.state.store.save(source)
        (config.workspace_root / source["id"]).mkdir(parents=True, exist_ok=True)
    with TestClient(create_app(settings(tmp_path, auto_resume=True), MockProvider())) as client:
        tasks = client.get("/api/tasks").json()["tasks"]
        resumed = [task for task in tasks if task.get("auto_recovery")]
        assert len(resumed) == 1
        assert resumed[0]["continue_from"] == source["id"]
        assert "backend restart" in resumed[0]["auto_recovery"]["reason"]
        assert wait_task(client, resumed[0]["id"])["status"] in {"completed", "failed"}


def test_auto_resume_stops_at_the_cap(tmp_path):
    config = settings(tmp_path)
    with TestClient(create_app(config, MockProvider())) as client:
        source = client.app.state.store.create("Interrupted", "test/model")
        source["status"] = "running"
        client.app.state.store.save(source)
        (config.workspace_root / source["id"]).mkdir(parents=True, exist_ok=True)
    with TestClient(create_app(settings(tmp_path, auto_resume=True, max_auto_recovery=0),
                               MockProvider())) as client:
        tasks = client.get("/api/tasks").json()["tasks"]
        assert [task["id"] for task in tasks] == [source["id"]]


class StuckProvider(MockProvider):
    """A Coder that repeats one failing edit until the loop guard stops it."""

    async def complete(self, model, messages, tools):
        if "You are Coder." in messages[0]["content"]:
            return {"content": None, "tool_calls": [{
                "id": "stuck", "type": "function",
                "function": {"name": "edit_file", "arguments": json.dumps(
                    {"path": "main.py", "old_text": "not in the file", "new_text": "x"})}}]}
        return await super().complete(model, messages, tools)


def test_a_stuck_run_continues_itself_only_when_enabled(tmp_path):
    """Covers R16: the continuation is bounded, recorded, and opt-in."""
    with TestClient(create_app(settings(tmp_path), StuckProvider())) as client:
        task_id = client.post("/api/tasks", json={"task": "Get stuck"}).json()["id"]
        value = wait_task(client, task_id)
        assert value["status"] == "failed"
        assert "without reaching a conclusion" in value["error"]
        assert [task["id"] for task in client.get("/api/tasks").json()["tasks"]] == [task_id]


def test_a_stuck_run_starts_one_bounded_continuation_when_enabled(tmp_path):
    config = settings(tmp_path, auto_continue=True, max_auto_recovery=1)
    with TestClient(create_app(config, StuckProvider())) as client:
        task_id = client.post("/api/tasks", json={"task": "Get stuck"}).json()["id"]
        value = wait_task(client, task_id)
        assert value["status"] == "failed"
        deadline = time.monotonic() + 10
        continued = []
        while time.monotonic() < deadline and not continued:
            continued = [task for task in client.get("/api/tasks").json()["tasks"]
                         if task.get("auto_recovery")]
            if not continued:
                time.sleep(0.05)
        assert len(continued) == 1, "automatic recovery must start exactly one continuation"
        assert continued[0]["continue_from"] == task_id
        assert continued[0]["continuation_depth"] == 1
        assert "without reaching a conclusion" in continued[0]["auto_recovery"]["reason"]
        # The chain stops here: the continuation fails the same way, and the cap keeps
        # it from starting a third task.
        wait_task(client, continued[0]["id"])
        time.sleep(0.2)
        assert len([task for task in client.get("/api/tasks").json()["tasks"]
                    if task.get("auto_recovery")]) == 1
