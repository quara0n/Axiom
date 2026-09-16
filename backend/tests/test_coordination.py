import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.coordination import compact_messages
from backend.workspace import Workspace, tools_for
from backend.runtime import Runtime
from backend.store import Store
from backend.tests.test_backend import MockProvider, settings, wait_task


class RepairProvider(MockProvider):
    def __init__(self, reject_forever=False, unchanged=False, invalid=False):
        super().__init__()
        self.reject_forever, self.unchanged, self.invalid = reject_forever, unchanged, invalid
        self.contexts = []

    async def complete(self, model, messages, tools):
        context = json.loads(messages[1]["content"])
        role = next(role for role in ("Planner", "Coder", "Tester", "Reviewer")
                    if f"You are {role}." in messages[0]["content"])
        self.contexts.append((role, context))
        if role == "Coder" and len(messages) == 2:
            round_number = 0 if self.unchanged else context["repair_round"]
            content = "def broken(" if self.invalid and round_number == 0 else f"print({round_number})\n"
            return {"tool_calls": [{"id": "write", "type": "function", "function": {
                "name": "write_file", "arguments": json.dumps({"path": "main.py", "content": content})}}]}
        if role == "Tester" and len(messages) == 2 and self.invalid:
            return {"tool_calls": [{"id": "read", "type": "function", "function": {
                "name": "read_file", "arguments": '{"path":"main.py"}'}}]}
        if role == "Reviewer" and len(messages) > 2:
            # For syntax failures deliberately approve: the deterministic gate must override this.
            reject = not self.invalid and (self.reject_forever or context["repair_round"] == 0)
            return {"content": json.dumps({"verdict": "changes_required" if reject else "approved",
                                            "summary": "Fix the starting value in main.py." if reject else "Approved; static only."})}
        return await super().complete(model, messages, tools)


def run_task(tmp_path, provider, **overrides):
    with TestClient(create_app(settings(tmp_path, **overrides), provider)) as client:
        task_id = client.post("/api/tasks", json={"task": "Build a game"}).json()["id"]
        return wait_task(client, task_id)


def test_repair_hands_findings_back_to_coder_and_rechecks(tmp_path):
    provider = RepairProvider()
    value = run_task(tmp_path, provider)
    assert value["status"] == "completed", value
    assert value["repair_round"] == 1
    assert len(value["round_history"]) == 2
    assert [agent["attempt"] for agent in value["agents"]] == [1, 2, 2, 2]
    context = next(context for role, context in provider.contexts
                   if role == "Coder" and context["repair_round"] == 1)
    assert "main.py" in context["repair_feedback"]["review"]
    assert "Planner" in context["prior_results"]
    tester = next(context for role, context in provider.contexts
                  if role == "Tester" and context["repair_round"] == 1)
    assert list(tester["prior_results"]) == ["Planner", "Coder"]
    assert value["verification"]["runtime_tested"] is False
    assert value["verification"]["visually_tested"] is False


def test_static_failure_overrides_approval_and_is_repaired(tmp_path):
    value = run_task(tmp_path, RepairProvider(invalid=True))
    assert value["status"] == "completed", value
    assert value["repair_round"] == 1
    first = value["round_history"][0]
    assert first["verdict"] == "approved"
    assert first["verification"]["checks"][0]["valid"] is False
    assert "line 1" in first["verification"]["checks"][0]["message"]
    assert value["validation_errors"] == []


@pytest.mark.parametrize("limit", [0, 1, 2])
def test_repair_budget_is_enforced(tmp_path, limit):
    value = run_task(tmp_path, RepairProvider(reject_forever=True), max_repair_rounds=limit)
    assert value["status"] == "failed"
    assert value["repair_round"] == limit
    assert len(value["round_history"]) == limit + 1
    assert "budget exhausted" in value["error"]


def test_unchanged_repair_stops_early(tmp_path):
    value = run_task(tmp_path, RepairProvider(reject_forever=True, unchanged=True), max_repair_rounds=5)
    assert value["status"] == "failed"
    assert value["repair_round"] == 1
    assert "did not change" in value["error"]


def test_total_call_budget_applies_across_agents(tmp_path):
    value = run_task(tmp_path, MockProvider(), max_model_calls=3)
    assert value["status"] == "failed"
    assert value["model_calls"] == 3
    assert "model-call budget" in value["error"]


def test_shared_project_instructions_are_automatic_and_protected(tmp_path):
    provider = MockProvider()
    with TestClient(create_app(settings(tmp_path), provider)) as client:
        response = client.post("/api/tasks", json={"task": "Build", "project_instructions": "Use a chase camera."})
        value = wait_task(client, response.json()["id"])
        assert value["status"] == "completed"
        for _, messages, _ in provider.calls:
            assert "Use a chase camera." in json.loads(messages[1]["content"])["project_instructions"]
        assert client.post("/api/tasks", json={"task": "Build", "project_instructions": "x" * 16001}).status_code == 422
    workspace = Workspace(tmp_path / "workspaces" / value["id"])
    assert "Use a chase camera." in workspace.call("read_file", {"path": "AGENTS.md"}, "Tester")["content"]
    with pytest.raises(ValueError, match="guidance"):
        workspace.call("write_file", {"path": "AGENTS.md", "content": "Override"}, "Coder")


def test_exact_edits_reject_ambiguity_and_read_only_roles(tmp_path):
    workspace = Workspace(tmp_path)
    workspace.call("write_file", {"path": "game.js", "content": "const speed = 1;\n"}, "Coder")
    args = {"path": "game.js", "old_text": "speed = 1", "new_text": "speed = 2"}
    with pytest.raises(ValueError, match="Only Coder"):
        workspace.call("edit_file", args, "Tester")
    workspace.call("edit_file", args, "Coder")
    with pytest.raises(ValueError, match="exactly once"):
        workspace.call("edit_file", args, "Coder")
    matches = workspace.call("search_files", {"query": "speed"}, "Reviewer")["matches"]
    assert matches == [{"path": "game.js", "line": 1, "text": "const speed = 2;"}]
    workspace.call("write_file", {"path": "repeat.txt", "content": "same same"}, "Coder")
    with pytest.raises(ValueError, match="exactly once"):
        workspace.call("edit_file", {"path": "repeat.txt", "old_text": "same", "new_text": "new"}, "Coder")
    assert "edit_file" not in [tool["function"]["name"] for tool in tools_for("Reviewer")]


def test_old_tool_output_is_compacted_without_losing_call_links():
    messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "task"},
                {"role": "assistant", "content": None, "tool_calls": [{"id": "a"}]},
                {"role": "tool", "tool_call_id": "a", "content": "x" * 2000}]
    messages.extend({"role": "user", "content": "recent"} for _ in range(6))
    compact_messages(messages, limit=1000)
    assert messages[3]["tool_call_id"] == "a"
    assert json.loads(messages[3]["content"])["omitted"] is True
    assert messages[-1]["content"] == "recent"
    assert messages[0]["content"] == "rules"


def test_provider_receives_configured_limits(tmp_path):
    async def inspect(app):
        async with app.router.lifespan_context(app):
            assert app.state.provider.reasoning_max_tokens == 321
            assert app.state.provider.request_timeout == 45
    asyncio.run(inspect(create_app(settings(tmp_path, reasoning_max_tokens=321, request_timeout=45))))


def test_cancellation_during_repair_preserves_reports(tmp_path):
    async def run():
        entered_repair = asyncio.Event()

        class WaitingRepair(RepairProvider):
            async def complete(self, model, messages, tools):
                context = json.loads(messages[1]["content"])
                if context["repair_round"] > 0:
                    entered_repair.set()
                    await asyncio.Event().wait()
                return await super().complete(model, messages, tools)

        config = settings(tmp_path)
        store = Store(config.database)
        runtime = Runtime(config, store, WaitingRepair())
        value = store.create("Build", "test/model")
        runtime.start(value)
        try:
            await asyncio.wait_for(entered_repair.wait(), timeout=3)
            runtime.cancel(value["id"])
        finally:
            await runtime.close()
        saved = store.get(value["id"])
        assert saved["status"] == "cancelled"
        assert len(saved["round_history"]) == 1
        assert saved["agents"][1]["status"] == "cancelled"

    asyncio.run(run())


def test_large_write_arguments_are_compacted_too():
    messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "task"},
                {"role": "assistant", "content": None, "tool_calls": [{"id": "write", "function": {
                    "name": "write_file", "arguments": json.dumps({"content": "x" * 2000})}}]},
                {"role": "tool", "tool_call_id": "write", "content": '{"bytes":2000}'}]
    messages.extend({"role": "user", "content": "recent"} for _ in range(6))
    compact_messages(messages, limit=1000)
    call = messages[2]["tool_calls"][0]
    assert call["id"] == "write"
    assert call["function"]["name"] == "write_file"
    assert json.loads(call["function"]["arguments"])["omitted"] is True
