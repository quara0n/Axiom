import asyncio
import json
import os
from pathlib import Path
import shutil
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


def test_local_connection_saves_key_without_exposing_it(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    key = "test-openrouter-key"
    with TestClient(create_app(settings(tmp_path, api_key=""))) as client:
        assert client.get("/api/health").json()["configured"] is False
        assert client.post("/api/connection", json={"api_key": key}, headers={
            "Origin": "https://evil.example"}).status_code == 403
        response = client.post("/api/connection", json={"api_key": key})
        assert response.status_code == 200
        assert response.json() == {"configured": True, "model": "openrouter/free"}
        assert client.get("/api/health").json()["configured"] is True
        assert key not in json.dumps(client.get("/api/health").json())
        assert key in (tmp_path / ".env").read_text()


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


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for JavaScript checks")
def test_static_validation_covers_javascript_html_and_rejects_other_types(tmp_path):
    workspace = Workspace(tmp_path / "workspace")
    workspace.call("write_file", {"path": "app.js", "content": "export const moves = [];\n"}, "Coder")
    assert workspace.call("validate_file", {"path": "app.js"}, "Tester")["valid"] is True
    workspace.call("write_file", {"path": "broken.js", "content": "function ( {\n"}, "Coder")
    with pytest.raises(ValueError):
        workspace.call("validate_file", {"path": "broken.js"}, "Tester")
    workspace.call("write_file", {"path": "index.html", "content":
                                  "<!doctype html><html><body><script>const board = 64;</script></body></html>"}, "Coder")
    assert workspace.call("validate_file", {"path": "index.html"}, "Tester")["valid"] is True
    workspace.call("write_file", {"path": "broken.html", "content":
                                  "<!doctype html><html><body><script>const board = ;</script></body></html>"}, "Coder")
    with pytest.raises(ValueError):
        workspace.call("validate_file", {"path": "broken.html"}, "Tester")
    workspace.call("write_file", {"path": "notes.txt", "content": "hello"}, "Coder")
    with pytest.raises(ValueError):
        workspace.call("validate_file", {"path": "notes.txt"}, "Tester")


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


def test_empty_response_is_retried_before_failing(tmp_path):
    class FlakyCoder(MockProvider):
        flaked = False

        async def complete(self, model, messages, tools):
            role = next(role for role in ("Planner", "Coder", "Tester", "Reviewer")
                        if f"You are {role}." in messages[0]["content"])
            if role == "Coder" and not any(message["role"] == "tool" for message in messages):
                if not self.flaked:
                    type(self).flaked = True
                    return {"content": "   "}
                return {"content": None, "tool_calls": [{
                    "id": "write", "type": "function",
                    "function": {"name": "write_file",
                                 "arguments": json.dumps({"path": "main.py", "content": "print('hello')\n"})}}]}
            return await super().complete(model, messages, tools)

    with TestClient(create_app(settings(tmp_path), FlakyCoder())) as client:
        task_id = client.post("/api/tasks", json={"task": "Create hello world"}).json()["id"]
        value = wait_task(client, task_id)
        assert value["status"] == "completed", value
        assert value["files"] == ["main.py"]


def test_repeated_empty_responses_fail_and_name_the_model(tmp_path):
    class EmptyProvider(MockProvider):
        async def complete(self, model, messages, tools):
            return {"content": None}

    with TestClient(create_app(settings(tmp_path), EmptyProvider())) as client:
        task_id = client.post("/api/tasks", json={"task": "Run", "model": "~broken/model"}).json()["id"]
        value = wait_task(client, task_id)
        assert value["status"] == "failed"
        assert "empty responses in a row" in value["error"]
        assert "~broken/model" in value["error"]


def test_response_cut_off_by_output_limit_says_so(tmp_path):
    class TruncatingProvider(MockProvider):
        async def complete(self, model, messages, tools):
            self.calls.append((model, messages.copy(), tools))
            return {"content": None, "finish_reason": "length"}

    config = settings(tmp_path, max_tokens=2048)
    with TestClient(create_app(config, TruncatingProvider())) as client:
        task_id = client.post("/api/tasks", json={"task": "Write a large program"}).json()["id"]
        value = wait_task(client, task_id)
        assert value["status"] == "failed"
        assert "output limit (2048 tokens)" in value["error"]
        assert "AXIOM_MAX_TOKENS" in value["error"]


def test_max_tokens_setting_is_bounded(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("AXIOM_MAX_TOKENS", "999999999")
    monkeypatch.setenv("AXIOM_REASONING_MAX_TOKENS", "999999999")
    monkeypatch.setenv("AXIOM_REQUEST_TIMEOUT", "5")
    monkeypatch.setenv("AXIOM_MAX_TOOL_ROUNDS", "9999")
    config = Settings.from_env()
    assert config.max_tokens == 200000
    assert config.reasoning_max_tokens == 100000
    assert config.request_timeout == 30
    assert config.max_tool_rounds == 120
    monkeypatch.setenv("AXIOM_MAX_TOKENS", "10")
    assert Settings.from_env().max_tokens == 1024


def test_cut_off_response_is_retried_with_a_corrective_nudge(tmp_path):
    class RecoveringProvider(MockProvider):
        truncated = False

        async def complete(self, model, messages, tools):
            role = next(role for role in ("Planner", "Coder", "Tester", "Reviewer")
                        if f"You are {role}." in messages[0]["content"])
            if not type(self).truncated:
                type(self).truncated = True
                return {"content": None, "finish_reason": "length"}
            if not any(message["role"] == "tool" for message in messages):
                name, args = {
                    "Planner": ("list_files", {}),
                    "Coder": ("write_file", {"path": "main.py", "content": "print('hello')\n"}),
                    "Tester": ("validate_file", {"path": "main.py"}),
                    "Reviewer": ("read_file", {"path": "main.py"}),
                }[role]
                return {"content": None, "tool_calls": [{
                    "id": role, "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)}}]}
            result = f"{role} complete; static checks only."
            return {"content": json.dumps({"verdict": "approved", "summary": result})
                    if role == "Reviewer" else result}

    with TestClient(create_app(settings(tmp_path), RecoveringProvider())) as client:
        task_id = client.post("/api/tasks", json={"task": "Create hello world"}).json()["id"]
        value = wait_task(client, task_id)
        assert value["status"] == "completed", value
        assert value["files"] == ["main.py"]


def test_each_agent_can_use_its_own_model(tmp_path):
    provider = MockProvider()
    with TestClient(create_app(settings(tmp_path), provider)) as client:
        response = client.post("/api/tasks", json={
            "task": "Create hello world", "model": "test/default",
            "models": {"Planner": "~planner/model", "Coder": "coder/model", "Tester": ""}})
        assert response.status_code == 202
        value = wait_task(client, response.json()["id"])
        assert value["status"] == "completed", value
        assert value["models"] == {"Planner": "~planner/model", "Coder": "coder/model"}
        assert [agent["model"] for agent in value["agents"]] == [
            "~planner/model", "coder/model", "test/default", "test/default"]
        used = {}
        for model, messages, _ in provider.calls:
            role = next(role for role in ("Planner", "Coder", "Tester", "Reviewer")
                        if f"You are {role}." in messages[0]["content"])
            used[role] = model
        assert used == {"Planner": "~planner/model", "Coder": "coder/model",
                        "Tester": "test/default", "Reviewer": "test/default"}


def test_unknown_agent_role_and_invalid_model_are_rejected(tmp_path):
    with TestClient(create_app(settings(tmp_path), MockProvider())) as client:
        assert client.post("/api/tasks", json={
            "task": "Run", "models": {"Coder2": "x/y"}}).status_code == 422
        assert client.post("/api/tasks", json={
            "task": "Run", "models": {"Coder": "bad model!"}}).status_code == 422
        assert client.post("/api/tasks", json={
            "task": "Run", "models": {"Coder": "   "}}).status_code == 202


def test_rejected_tool_call_names_the_tool_and_path(tmp_path):
    class BadPathProvider(MockProvider):
        async def complete(self, model, messages, tools):
            return {"content": None, "tool_calls": [{
                "id": "bad", "type": "function",
                "function": {"name": "read_file",
                             "arguments": json.dumps({"path": "../outside"})}}]}

    with TestClient(create_app(settings(tmp_path, max_tool_rounds=2), BadPathProvider())) as client:
        task_id = client.post("/api/tasks", json={"task": "Run"}).json()["id"]
        value = wait_task(client, task_id)
        assert value["status"] == "failed"
        rejected = [event["text"] for event in value["events"]
                    if event["text"].startswith("Tool call rejected")]
        assert rejected, value["events"]
        assert "read_file" in rejected[0]
        assert "../outside" in rejected[0]


def test_task_timeout(tmp_path):
    with TestClient(create_app(settings(tmp_path, task_timeout=0.03), MockProvider(delay=10))) as client:
        task_id = client.post("/api/tasks", json={"task": "Run"}).json()["id"]
        value = wait_task(client, task_id)
        assert value["status"] == "failed"
        assert "time limit" in value["error"]
