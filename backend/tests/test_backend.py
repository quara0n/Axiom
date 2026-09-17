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


def test_saving_an_older_task_preserves_recent_task_order(tmp_path):
    store = Store(tmp_path / "order.sqlite3")
    older = store.create("Older", "test/model")
    newer = store.create("Newer", "test/model")
    older["summary"] = "Progress update"
    store.save(older)
    assert [task["id"] for task in store.list()] == [newer["id"], older["id"]]
    assert store.get(older["id"])["summary"] == "Progress update"


def test_a_project_name_is_a_label_not_a_path(tmp_path):
    store = Store(tmp_path / "projects.sqlite3")
    assert store.create("Run", "test/model", project="  Varg  ")["project"] == "Varg"
    assert store.create("Run", "test/model")["project"] == "Axiom"
    assert store.create("Run", "test/model", project="   ")["project"] == "Axiom"
    assert store.create("Run", "test/model", project="x" * 400)["project"] == "x" * 60


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
        assert value["files"] == ["AGENTS.md", "main.py"]
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
    monkeypatch.setenv("AXIOM_CREDENTIALS", str(tmp_path / ".axiom" / "credentials.env"))
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
        # The key must not land in .env: `next dev` reloads the dashboard when a
        # watched .env file changes, which interrupted the save.
        assert key in (tmp_path / ".axiom" / "credentials.env").read_text()
        assert not (tmp_path / ".env").exists()


def test_saved_key_is_loaded_but_the_environment_still_wins(tmp_path, monkeypatch):
    credentials = tmp_path / "saved.env"
    credentials.write_text("OPENROUTER_API_KEY='saved-in-dashboard'\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AXIOM_CREDENTIALS", str(credentials))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert Settings.from_env().api_key == "saved-in-dashboard"
    monkeypatch.setenv("OPENROUTER_API_KEY", "from-the-environment")
    assert Settings.from_env().api_key == "from-the-environment"


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


def test_restart_marks_unfinished_tasks_resumable(tmp_path):
    # A restart keeps the workspace, so the run is interrupted rather than lost.
    store = Store(tmp_path / "db.sqlite3")
    value = store.create("unfinished", "test/model")
    assert store.recover() == [value["id"]]
    interrupted = store.get(value["id"])
    assert interrupted["status"] == "interrupted"
    assert "Resume it" in interrupted["error"]


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


def test_inspection_rounds_are_bounded_by_the_model_call_budget(tmp_path):
    class LoopProvider(MockProvider):
        def __init__(self):
            super().__init__()
            self.round = 0

        async def complete(self, model, messages, tools):
            # Different query every round: this is a diligent agent, not a loop.
            self.round += 1
            return {"content": None, "tool_calls": [{
                "id": "loop", "type": "function",
                "function": {"name": "search_files",
                             "arguments": json.dumps({"query": f"needle-{self.round}"})},
            }]}
    # Reading files is not a runaway loop: the task-wide call budget stops it, and the
    # per-agent round limit is reserved for rounds that change the project.
    with TestClient(create_app(settings(tmp_path, max_tool_rounds=2, max_model_calls=6),
                               LoopProvider())) as client:
        task_id = client.post("/api/tasks", json={"task": "Run"}).json()["id"]
        value = wait_task(client, task_id)
        assert value["status"] == "failed"
        assert "model-call budget" in value["error"]


def test_repeated_identical_calls_stop_the_agent_early(tmp_path):
    class RepeatProvider(MockProvider):
        async def complete(self, model, messages, tools):
            return {"content": None, "tool_calls": [{
                "id": "again", "type": "function",
                "function": {"name": "read_file", "arguments": json.dumps({"path": "AGENTS.md"})},
            }]}

    with TestClient(create_app(settings(tmp_path, max_model_calls=80), RepeatProvider())) as client:
        task_id = client.post("/api/tasks", json={"task": "Run"}).json()["id"]
        value = wait_task(client, task_id)
        assert value["status"] == "failed"
        assert "without reaching a conclusion" in value["error"]
        # It must stop on the repeated call, not by draining the task budget.
        assert value["model_calls"] < 15, value["model_calls"]


def test_work_round_limit_stops_an_agent_that_keeps_changing_the_project(tmp_path):
    class WriteLoopProvider(MockProvider):
        async def complete(self, model, messages, tools):
            return {"content": None, "tool_calls": [{
                "id": "loop", "type": "function",
                "function": {"name": "write_file",
                             "arguments": json.dumps({"path": "main.py", "content": "print(1)\n"})},
            }]}
    with TestClient(create_app(settings(tmp_path, max_tool_rounds=2), WriteLoopProvider())) as client:
        task_id = client.post("/api/tasks", json={"task": "Run"}).json()["id"]
        value = wait_task(client, task_id)
        assert value["status"] == "failed"
        assert "work round limit" in value["error"]
        assert "AXIOM_MAX_TOOL_ROUNDS" in value["error"]


def test_distinct_edits_to_the_same_file_are_not_a_repeat_loop(tmp_path):
    class IncrementalProvider(MockProvider):
        async def complete(self, model, messages, tools):
            if "You are Coder." in messages[0]["content"]:
                count = sum(message["role"] == "tool" for message in messages)
                if count < 7:
                    name = "write_file" if count == 0 else "edit_file"
                    args = {"path": "main.py", "content": "value = 0\n"} if count == 0 else {
                        "path": "main.py", "old_text": f"value = {count - 1}",
                        "new_text": f"value = {count}"}
                    return {"content": None, "tool_calls": [{
                        "id": f"step-{count}", "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args)}}]}
            return await super().complete(model, messages, tools)

    config = settings(tmp_path, max_tool_rounds=20)
    with TestClient(create_app(config, IncrementalProvider())) as client:
        task_id = client.post("/api/tasks", json={"task": "Build incrementally"}).json()["id"]
        value = wait_task(client, task_id)
        assert value["status"] == "completed", value
        assert (config.workspace_root / task_id / "main.py").read_text() == "value = 6\n"


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


def test_a_fenced_reviewer_verdict_is_still_a_verdict(tmp_path):
    """A run that solved its task must not be failed because the model wrapped its
    JSON in a markdown fence."""
    class FencedProvider(MockProvider):
        async def complete(self, model, messages, tools):
            result = await super().complete(model, messages, tools)
            if len(messages) > 2 and "You are Reviewer." in messages[0]["content"]:
                return {"content": "```json\n"
                        + json.dumps({"verdict": "approved", "summary": "Fine."})
                        + "\n```"}
            return result

    with TestClient(create_app(settings(tmp_path), FencedProvider())) as client:
        task_id = client.post("/api/tasks", json={"task": "Run"}).json()["id"]
        value = wait_task(client, task_id)
        assert value["status"] == "completed", value.get("error")
        assert value["review_verdict"] == "approved"
        assert value["summary"] == "Fine."


def test_a_finished_thread_is_deleted_with_the_workspace_it_wrote(tmp_path):
    config = settings(tmp_path)
    with TestClient(create_app(config, MockProvider())) as client:
        task_id = client.post("/api/tasks", json={"task": "Run"}).json()["id"]
        assert wait_task(client, task_id)["status"] == "completed"
        workspace = config.workspace_root / task_id
        assert workspace.is_dir()

        response = client.delete(f"/api/tasks/{task_id}")
        assert response.status_code == 200
        assert response.json() == {"deleted": task_id}
        assert client.get(f"/api/tasks/{task_id}").status_code == 404
        assert not workspace.exists()


def test_a_running_thread_is_not_deleted(tmp_path):
    """Deleting the record while a job is still writing would leave it with no owner."""
    config = settings(tmp_path)
    with TestClient(create_app(config, MockProvider(delay=1.5))) as client:
        task_id = client.post("/api/tasks", json={"task": "Run"}).json()["id"]
        response = client.delete(f"/api/tasks/{task_id}")
        assert response.status_code == 409
        assert "Cancel" in response.json()["detail"]
        assert client.get(f"/api/tasks/{task_id}").status_code == 200


def test_deleting_a_thread_that_is_not_there_is_a_404(tmp_path):
    with TestClient(create_app(settings(tmp_path), MockProvider())) as client:
        assert client.delete("/api/tasks/0123456789abcdef0123456789abcdef").status_code == 404


def test_a_thread_is_filed_under_the_project_it_was_started_in(tmp_path):
    with TestClient(create_app(settings(tmp_path), MockProvider())) as client:
        filed = client.post("/api/tasks", json={"task": "Run", "project": "Varg"}).json()
        assert filed["project"] == "Varg"
        assert wait_task(client, filed["id"])["status"] == "completed"
        default = client.post("/api/tasks", json={"task": "Run"}).json()
        assert default["project"] == "Axiom"
        assert wait_task(client, default["id"])["status"] == "completed"


def test_a_malformed_verdict_is_asked_for_once_then_fails(tmp_path):
    class ProseProvider(MockProvider):
        def __init__(self):
            super().__init__()
            self.verdict_calls = 0

        async def complete(self, model, messages, tools):
            # Past the first inspection the Reviewer is asked again, so this provider
            # answers directly instead of leaning on the mock's shape assertions.
            if len(messages) > 2 and "You are Reviewer." in messages[0]["content"]:
                self.verdict_calls += 1
                return {"content": "Overall the implementation looks fine to me."}
            return await super().complete(model, messages, tools)

    provider = ProseProvider()
    with TestClient(create_app(settings(tmp_path), provider)) as client:
        task_id = client.post("/api/tasks", json={"task": "Run"}).json()["id"]
        value = wait_task(client, task_id)
        assert value["status"] == "failed"
        assert "structured verdict" in value["error"]
        # One answer, one retry. A model that cannot produce the object at all is
        # still a failed run, just not one that failed on the first slip.
        assert provider.verdict_calls == 2


def test_a_verdict_slip_does_not_fail_a_run_that_solved_its_task(tmp_path):
    class SlipProvider(MockProvider):
        def __init__(self):
            super().__init__()
            self.verdict_calls = 0

        async def complete(self, model, messages, tools):
            if len(messages) > 2 and "You are Reviewer." in messages[0]["content"]:
                self.verdict_calls += 1
                if self.verdict_calls == 1:
                    return {"content": "Verdict: approved. Summary: everything is fine."}
                return {"content": json.dumps({"verdict": "approved",
                                               "summary": "Approved after a retry."})}
            return await super().complete(model, messages, tools)

    provider = SlipProvider()
    with TestClient(create_app(settings(tmp_path), provider)) as client:
        task_id = client.post("/api/tasks", json={"task": "Run"}).json()["id"]
        value = wait_task(client, task_id)
        assert value["status"] == "completed", value.get("error")
        assert value["review_verdict"] == "approved"
        assert provider.verdict_calls == 2


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
        assert value["files"] == ["AGENTS.md", "main.py"]


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
    assert config.max_tool_rounds == 200
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
        assert value["files"] == ["AGENTS.md", "main.py"]


def test_cut_off_reporting_role_is_asked_for_a_shorter_report(tmp_path):
    class NudgeRecorder(MockProvider):
        def __init__(self):
            super().__init__()
            self.nudges, self.cut = [], False

        async def complete(self, model, messages, tools):
            role = next(name for name in ("Planner", "Coder", "Tester", "Reviewer")
                        if f"You are {name}." in messages[0]["content"])
            last = messages[-1]
            if last["role"] == "user" and last["content"].startswith(
                    ("Do not explain", "Your previous response was cut off")):
                self.nudges.append((role, last["content"]))
            if role == "Planner" and not self.cut:
                self.cut = True
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

    provider = NudgeRecorder()
    with TestClient(create_app(settings(tmp_path), provider)) as client:
        task_id = client.post("/api/tasks", json={"task": "Write a program"}).json()["id"]
        value = wait_task(client, task_id)
        assert value["status"] == "completed", value
    assert provider.nudges, "the cut-off Planner should have been nudged"
    role, text = provider.nudges[0]
    assert role == "Planner"
    assert "much shorter" in text


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
        # Only the agent that was working counts as failed; the rest never started.
        assert value["agents"][0]["status"] == "failed"
        assert [agent["status"] for agent in value["agents"][1:]] == ["pending"] * 3
        assert "workspace" in value["error"]


def test_task_timeout_defaults_are_generous_and_bounded(monkeypatch):
    assert Settings().task_timeout == 1800
    monkeypatch.setenv("AXIOM_TASK_TIMEOUT", "99999")
    assert Settings.from_env().task_timeout == 7200
    monkeypatch.setenv("AXIOM_TASK_TIMEOUT", "1")
    assert Settings.from_env().task_timeout == 30


def test_graph_keeps_concurrent_task_context_isolated(tmp_path):
    from backend.runtime import Runtime

    class ContextProvider(MockProvider):
        async def complete(self, model, messages, tools):
            response = await super().complete(model, messages, tools)
            if not response.get("tool_calls"):
                task = json.loads(messages[1]["content"])["task"]
                content = response["content"]
                if "You are Reviewer." in messages[0]["content"]:
                    report = json.loads(content)
                    report["summary"] += " " + task
                    response["content"] = json.dumps(report)
                else:
                    response["content"] += " " + task
            return response

    config, provider = settings(tmp_path), ContextProvider(delay=0.001)
    store = Store(config.database)
    runtime = Runtime(config, store, provider)
    values = [store.create(task, "test/model") for task in ("alpha", "beta")]

    async def run_both():
        await asyncio.gather(*(runtime.run(value) for value in values))

    asyncio.run(run_both())
    assert all(store.get(value["id"])["status"] == "completed" for value in values)
    roles = ("Planner", "Coder", "Tester", "Reviewer")
    for _, messages, _ in provider.calls:
        context = json.loads(messages[1]["content"])
        role = next(role for role in roles if f"You are {role}." in messages[0]["content"])
        assert list(context["prior_results"]) == list(roles[:roles.index(role)])
        assert all(result.endswith(context["task"]) for result in context["prior_results"].values())
