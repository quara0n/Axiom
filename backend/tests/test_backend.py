import asyncio
import json
import os
from pathlib import Path
import subprocess
import time

from fastapi.testclient import TestClient
import httpx
import pytest

from backend.app import create_app
from backend.config import Settings
from backend.provider import OpenRouter, ProviderError
from backend.store import Store
from backend.workspace import Workspace


class MockProvider:
    def __init__(self, fail=False, delay=0):
        self.calls = []
        self.fail = fail
        self.delay = delay

    async def close(self):
        pass

    async def models(self):
        return [{"id": "test/model", "name": "Test"}]

    async def complete(self, model, messages, tools):
        self.calls.append((model, messages.copy(), tools))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise ProviderError("Provider unavailable.")
        role = next(role for role in ("Planner", "Coder", "Tester", "Reviewer")
                    if f"You are {role}." in messages[0]["content"])
        if len(messages) == 2:
            name, args = {
                "Planner": ("list_files", {}),
                "Coder": ("write_file", {"path": "main.py", "content": "print('hello')\n"}),
                "Tester": ("validate_file", {"path": "main.py"}),
                "Reviewer": ("read_file", {"path": "main.py"}),
            }[role]
            return {"content": None, "tool_calls": [{
                "id": role, "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }]}
        assert messages[-1]["role"] == "tool"
        assert "error" not in json.loads(messages[-1]["content"])
        result = f"{role} complete; static checks only."
        return {"content": json.dumps({"verdict": "approved", "summary": result}) if role == "Reviewer" else result}


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
    pytest.fail("Task did not reach terminal state")


def test_real_api_persistence_and_all_agent_tool_loops(tmp_path):
    config, provider = settings(tmp_path), MockProvider()
    with TestClient(create_app(config, provider)) as client:
        assert client.get("/api/health").json()["configured"] is True
        response = client.post("/api/tasks", json={"task": "Create hello world", "model": "test/model"})
        assert response.status_code == 202
        task_id = response.json()["id"]
        value = wait_task(client, task_id)
        assert value["status"] == "completed", value
        assert [agent["status"] for agent in value["agents"]] == ["completed"] * 4
        assert value["files"] == ["main.py"]
        assert Path(value["workspace"]) == config.workspace_root / task_id
        assert len(provider.calls) == 8
        assert "Reviewer complete" in value["summary"]
        assert (config.workspace_root / task_id / "main.py").read_text() == "print('hello')\n"
        assert len(value["events"]) >= 12
        assert "test-backend-secret" not in json.dumps(value)
        assert client.get("/api/tasks").json()["tasks"][0]["id"] == task_id
        assert client.get("/api/models").json()["models"][0]["id"] == "test/model"
    with TestClient(create_app(config, MockProvider())) as client:
        assert client.get(f"/api/tasks/{task_id}").json()["status"] == "completed"


def test_provider_failure_is_persisted(tmp_path):
    with TestClient(create_app(settings(tmp_path), MockProvider(fail=True))) as client:
        task_id = client.post("/api/tasks", json={"task": "Run"}).json()["id"]
        value = wait_task(client, task_id)
        assert value["status"] == "failed"
        assert value["error"] == "Provider unavailable."
        assert value["agents"][0]["status"] == "failed"


def test_cancellation(tmp_path):
    with TestClient(create_app(settings(tmp_path), MockProvider(delay=10))) as client:
        task_id = client.post("/api/tasks", json={"task": "Run"}).json()["id"]
        assert client.post(f"/api/tasks/{task_id}/cancel").status_code == 200
        assert wait_task(client, task_id)["status"] == "cancelled"


def test_missing_key_and_cross_origin_mutation(tmp_path):
    with TestClient(create_app(settings(tmp_path, api_key=""), MockProvider())) as client:
        assert client.get("/api/health").json()["configured"] is False
        assert client.post("/api/tasks", json={"task": "Run"}).status_code == 503
        assert client.post("/api/tasks", json={"task": "Run"}, headers={
            "Origin": "https://evil.example"}).status_code == 403
        assert client.post("/api/tasks", json={"task": "Run"}, headers={
            "Host": "evil.example"}).status_code == 400


@pytest.mark.parametrize("path", [
    "../outside", "/etc/passwd", "C:/Windows/test", "C:secret", "\\\\server\\share",
    "a/../secret", ".env", ".git/config", "a:stream", "NUL.txt", "a./file", "a\\..\\b",
])
def test_workspace_rejects_escape_and_windows_special_paths(tmp_path, path):
    workspace = Workspace(tmp_path / "workspace")
    with pytest.raises(ValueError):
        workspace.call("write_file", {"path": path, "content": "bad"}, "Coder")


def test_read_only_agents_and_syntax_failure(tmp_path):
    workspace = Workspace(tmp_path / "workspace")
    with pytest.raises(ValueError):
        workspace.call("write_file", {"path": "main.py", "content": ""}, "Reviewer")
    workspace.call("write_file", {"path": "main.py", "content": "def broken("}, "Coder")
    with pytest.raises(SyntaxError):
        workspace.call("validate_file", {"path": "main.py"}, "Tester")


def test_symlink_or_junction_rejected(tmp_path):
    root, outside = tmp_path / "workspace", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    link = root / "escape"
    if os.name == "nt":
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
        assert result.returncode == 0, result.stderr
    else:
        link.symlink_to(outside, target_is_directory=True)
    workspace = Workspace(root)
    with pytest.raises(ValueError):
        workspace.path("escape/secret.txt")
    with pytest.raises(ValueError):
        workspace.files()


def test_restart_marks_unfinished_tasks_failed(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    value = store.create("unfinished", "test/model")
    store.recover()
    assert store.get(value["id"])["status"] == "failed"


def test_provider_error_does_not_expose_secrets():
    async def run():
        provider = OpenRouter("private-key")
        await provider.client.aclose()
        provider.client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(401, json={"error": "private-key"})),
            base_url="https://openrouter.ai/api/v1",
        )
        try:
            with pytest.raises(ProviderError) as caught:
                await provider.complete("test/model", [], [])
            assert "private-key" not in str(caught.value)
            assert "401" in str(caught.value)
        finally:
            await provider.close()
    asyncio.run(run())


def test_tool_round_limit(tmp_path):
    class LoopProvider(MockProvider):
        async def complete(self, model, messages, tools):
            return {"content": None, "tool_calls": [{
                "id": "loop", "type": "function",
                "function": {"name": "list_files", "arguments": "{}"},
            }]}
    with TestClient(create_app(settings(tmp_path, max_tool_rounds=2), LoopProvider())) as client:
        task_id = client.post("/api/tasks", json={"task": "Run"}).json()["id"]
        value = wait_task(client, task_id)
        assert value["status"] == "failed"
        assert "tool round limit" in value["error"]


def test_reviewer_can_reject_implementation(tmp_path):
    class RejectProvider(MockProvider):
        async def complete(self, model, messages, tools):
            result = await super().complete(model, messages, tools)
            if len(messages) > 2 and "You are Reviewer." in messages[0]["content"]:
                result = {"content": json.dumps({"verdict": "changes_required", "summary": "Missing requirement."})}
            return result
    with TestClient(create_app(settings(tmp_path), RejectProvider())) as client:
        task_id = client.post("/api/tasks", json={"task": "Run", "model": "~openai/test"}).json()["id"]
        value = wait_task(client, task_id)
        assert value["status"] == "failed"
        assert value["review_verdict"] == "changes_required"
        assert value["summary"] == "Missing requirement."


def test_no_tool_evidence_cannot_report_success(tmp_path):
    class NoToolsProvider(MockProvider):
        async def complete(self, model, messages, tools):
            return {"content": "Everything works"}
    with TestClient(create_app(settings(tmp_path, max_tool_rounds=2), NoToolsProvider())) as client:
        task_id = client.post("/api/tasks", json={"task": "Run"}).json()["id"]
        assert wait_task(client, task_id)["status"] == "failed"


def test_task_timeout(tmp_path):
    with TestClient(create_app(settings(tmp_path, task_timeout=0.03), MockProvider(delay=10))) as client:
        task_id = client.post("/api/tasks", json={"task": "Run"}).json()["id"]
        value = wait_task(client, task_id)
        assert value["status"] == "failed"
        assert "time limit" in value["error"]
