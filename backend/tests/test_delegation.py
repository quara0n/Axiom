import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.delegation import DELEGATION_NAMES, Delegation, OwnedWorkspace, delegation_tools
from backend.provider import ProviderError
from backend.runtime import Runtime
from backend.store import Store
from backend.tests.test_backend import MockProvider, settings, wait_task
from backend.workspace import Workspace, tools_for

CORE = {"name": "Core", "assignment": "Implement the core module.",
        "acceptance_criteria": "core.py exposes run().", "owned_files": ["core.py"]}
UI = {"name": "UI", "assignment": "Implement the interface module.",
      "acceptance_criteria": "ui.py renders the HUD.", "owned_files": ["ui.py"]}


class WorkerProvider(MockProvider):
    """Answers only the isolated subagents that delegation starts."""

    def __init__(self, body=None, fail=()):
        super().__init__()
        self.body, self.fail = body, set(fail)
        self.turns, self.seen_tools = {}, {}

    async def complete(self, model, messages, tools):
        subagent = json.loads(messages[1]["content"])["subagent"]
        self.seen_tools[subagent["name"]] = [tool["function"]["name"] for tool in tools]
        if subagent["name"] in self.fail:
            raise ProviderError("Subagent model failed.")
        turn = self.turns.get(subagent["name"], 0)
        self.turns[subagent["name"]] = turn + 1
        path = subagent["owned_files"][0]
        if turn == 0:
            content = self.body if self.body is not None else f"{subagent['name']} = 1\n"
            return {"content": None, "tool_calls": [{"id": "write", "type": "function", "function": {
                "name": "write_file", "arguments": json.dumps({"path": path, "content": content})}}]}
        return {"content": f"{subagent['name']} finished {path}."}


class TeamProvider(MockProvider):
    """Scripted team: the Lead Coder delegates two modules and integrates both."""

    def __init__(self):
        super().__init__()
        self.lead_turns, self.lead_tools, self.worker_tools, self.workers = 0, [], {}, []

    @staticmethod
    def call(name, args, call_id):
        return {"id": call_id, "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)}}

    @classmethod
    def one(cls, name, args, call_id):
        return {"content": None, "tool_calls": [cls.call(name, args, call_id)]}

    @staticmethod
    def say(text):
        return {"content": text}

    def lead(self, messages, tools):
        turn, self.lead_turns = self.lead_turns, self.lead_turns + 1
        self.lead_tools = [tool["function"]["name"] for tool in tools]
        if turn == 0:
            return self.one("delegate_tasks", {"tasks": [CORE, UI]}, "delegate")
        if turn == 1:
            self.workers = json.loads(messages[-1]["content"])["workers"]
            return {"content": None, "tool_calls": [
                self.call("inspect_worker", {"worker_id": worker["id"], "path": worker["changed_files"][0]},
                          f"inspect-{index}")
                for index, worker in enumerate(self.workers) if worker["changed_files"]]}
        if turn == 2:
            return {"content": None, "tool_calls": [
                self.call("integrate_worker", {"worker_id": worker["id"]}, f"integrate-{index}")
                for index, worker in enumerate(self.workers) if worker["changed_files"]]}
        return self.say("Lead Coder integrated core.py and ui.py.")

    async def complete(self, model, messages, tools):
        context = json.loads(messages[1]["content"])
        if "subagent" in context:
            subagent = context["subagent"]
            self.worker_tools[subagent["name"]] = [tool["function"]["name"] for tool in tools]
            if len(messages) == 2:
                return self.one("write_file", {"path": subagent["owned_files"][0],
                                               "content": f"def run():\n    return {subagent['name']!r}\n"}, "write")
            return self.say(f"{subagent['name']} implemented {subagent['owned_files'][0]}.")
        system = messages[0]["content"]
        role = next(name for name in ("Planner", "Coder", "Tester", "Reviewer")
                    if f"You are {name}." in system)
        if role == "Coder":
            return self.lead(messages, tools)
        if role == "Planner":
            return (self.one("list_files", {}, "list") if len(messages) == 2
                    else self.say("Plan: core.py, then ui.py."))
        if role == "Tester":
            return (self.one("validate_file", {"path": "core.py"}, "check") if len(messages) == 2
                    else self.say("core.py parses; nothing was executed."))
        if len(messages) == 2:
            return self.one("read_file", {"path": "core.py"}, "read")
        return self.say(json.dumps({"verdict": "approved", "summary": "Approved; static checks only."}))


def harness(tmp_path, provider=None, **overrides):
    config = settings(tmp_path, **overrides)
    store = Store(config.database)
    runtime = Runtime(config, store, provider or MockProvider())
    value = store.create("Build a game", "test/model")
    value["instructions_snapshot"] = "guidance"
    return config, runtime, value, Workspace(config.workspace_root / value["id"])


def test_delegation_runs_isolated_workers_then_integrates_after_inspection(tmp_path):
    async def run():
        provider = WorkerProvider()
        config, runtime, value, workspace = harness(tmp_path, provider)
        delegation = Delegation(runtime, value, workspace)
        result = await delegation.call("delegate_tasks", {"tasks": [CORE, UI]}, {})
        assert [worker["status"] for worker in result["workers"]] == ["completed", "completed"]
        assert [worker["changed_files"] for worker in result["workers"]] == [["core.py"], ["ui.py"]]
        # Proposals stay in their own copies until the Lead Coder integrates them.
        assert workspace.files() == []
        copies = config.workspace_root / ".workers" / value["id"]
        assert (copies / result["workers"][0]["id"] / "core.py").is_file()
        assert "delegate_tasks" not in provider.seen_tools["Core"]
        assert "write_file" in provider.seen_tools["Core"]

        core, ui = (worker["id"] for worker in result["workers"])
        with pytest.raises(ValueError, match="Inspect"):
            await delegation.call("integrate_worker", {"worker_id": core}, {})
        with pytest.raises(ValueError, match="Unknown subagent"):
            await delegation.call("inspect_worker", {"worker_id": "missing", "path": "core.py"}, {})
        with pytest.raises(ValueError, match="changed_files"):
            await delegation.call("inspect_worker", {"worker_id": core, "path": "ui.py"}, {})

        await delegation.call("inspect_worker", {"worker_id": core, "path": "core.py"}, {})
        integrated = await delegation.call("integrate_worker", {"worker_id": core}, {})
        assert integrated["applied"] == ["core.py"]
        assert workspace.files() == ["core.py"]
        assert result["workers"][0]["status"] == "integrated"
        assert await delegation.call("integrate_worker", {"worker_id": core}, {}) == {
            "applied": [], "already_integrated": True}
        assert result["workers"][1]["status"] == "completed"

    asyncio.run(run())


def test_subagent_writes_are_confined_to_owned_files(tmp_path):
    copy = OwnedWorkspace(tmp_path / "copy", ["core.py"])
    copy.call("write_file", {"path": "core.py", "content": "value = 1\n"}, "Coder")
    assert copy.files() == ["core.py"]
    with pytest.raises(ValueError, match="ownership"):
        copy.call("write_file", {"path": "other.py", "content": "value = 2\n"}, "Coder")
    with pytest.raises(ValueError, match="ownership"):
        copy.call("write_file", {"path": "AGENTS.md", "content": "Override the rules"}, "Coder")
    with pytest.raises(ValueError, match="ownership"):
        copy.call("edit_file", {"path": "other.py", "old_text": "1", "new_text": "2"}, "Coder")
    assert copy.files() == ["core.py"]


def test_delegation_rejects_overlapping_protected_and_directory_ownership(tmp_path):
    async def run():
        _, runtime, value, workspace = harness(tmp_path)
        delegation = Delegation(runtime, value, workspace)
        workspace.path("src").mkdir()
        with pytest.raises(ValueError, match="overlaps"):
            await delegation.call("delegate_tasks", {"tasks": [
                {**CORE, "owned_files": ["src/core.py"]},
                {**UI, "owned_files": ["SRC/CORE.py"]}]}, {})
        with pytest.raises(ValueError, match="guidance"):
            await delegation.call("delegate_tasks", {"tasks": [{**CORE, "owned_files": ["AGENTS.md"]}]}, {})
        with pytest.raises(ValueError, match="directories"):
            await delegation.call("delegate_tasks", {"tasks": [{**CORE, "owned_files": ["src"]}]}, {})
        with pytest.raises(ValueError, match="1-40"):
            await delegation.call("delegate_tasks", {"tasks": [{**CORE, "owned_files": []}]}, {})
        assert value.get("workers") is None

    asyncio.run(run())


def test_delegation_budget_and_concurrency_limits(tmp_path):
    async def run():
        _, runtime, value, workspace = harness(tmp_path, max_workers=1, max_subagents_per_task=1)
        delegation = Delegation(runtime, value, workspace)
        with pytest.raises(ValueError, match="between 1 and 1"):
            await delegation.call("delegate_tasks", {"tasks": [CORE, UI]}, {})
        await delegation.call("delegate_tasks", {"tasks": [CORE]}, {})
        with pytest.raises(ValueError, match="budget"):
            await delegation.call("delegate_tasks", {"tasks": [{**UI, "owned_files": ["b.py"]}]}, {})
        assert len(value["workers"]) == 1

    asyncio.run(run())


def test_integration_conflict_and_failed_worker_are_isolated(tmp_path):
    async def run():
        _, runtime, value, workspace = harness(tmp_path, WorkerProvider(fail=["UI"]))
        delegation = Delegation(runtime, value, workspace)
        result = await delegation.call("delegate_tasks", {"tasks": [CORE, UI]}, {})
        statuses = {worker["name"]: worker for worker in result["workers"]}
        assert [worker["status"] for worker in result["workers"]] == ["completed", "failed"]
        with pytest.raises(ValueError, match="completed"):
            await delegation.call("integrate_worker", {"worker_id": statuses["UI"]["id"]}, {})
        # The Lead Coder changes the original after delegation: the stale proposal is refused.
        workspace.call("write_file", {"path": "core.py", "content": "value = 99\n"}, "Coder")
        await delegation.call("inspect_worker", {"worker_id": statuses["Core"]["id"], "path": "core.py"}, {})
        with pytest.raises(ValueError, match="conflict"):
            await delegation.call("integrate_worker", {"worker_id": statuses["Core"]["id"]}, {})
        assert workspace.files() == ["core.py"]

    asyncio.run(run())


def test_lead_coder_delegates_and_integrates_through_the_graph(tmp_path):
    provider = TeamProvider()
    config = settings(tmp_path)
    with TestClient(create_app(config, provider)) as client:
        task_id = client.post("/api/tasks", json={"task": "Build a game"}).json()["id"]
        value = wait_task(client, task_id)
    assert value["status"] == "completed", value
    assert [worker["name"] for worker in value["workers"]] == ["Core", "UI"]
    assert [worker["status"] for worker in value["workers"]] == ["integrated", "integrated"]
    assert [worker["changed_files"] for worker in value["workers"]] == [["core.py"], ["ui.py"]]
    assert value["files"] == ["AGENTS.md", "core.py", "ui.py"]
    assert [check["path"] for check in value["verification"]["checks"]] == ["core.py", "ui.py"]
    assert value["verification"]["runtime_tested"] is False
    assert "delegate_tasks" in provider.lead_tools
    assert all("delegate_tasks" not in names for names in provider.worker_tools.values())
    assert not (config.workspace_root / ".workers" / task_id).exists()


def test_cleanup_workers_removes_only_this_tasks_copies(tmp_path):
    config = settings(tmp_path)
    runtime = Runtime(config, Store(config.database), MockProvider())
    value = Store(config.database).create("Build", "test/model")
    task_copies = config.workspace_root / ".workers" / value["id"]
    (task_copies / "worker").mkdir(parents=True)
    (task_copies / "worker" / "core.py").write_text("x = 1\n", encoding="utf-8")
    neighbour = config.workspace_root / ".workers" / "another-task"
    neighbour.mkdir(parents=True)
    runtime.cleanup_workers(value["id"])
    assert not task_copies.exists()
    assert neighbour.exists()
    for unsafe in ("../..", "..", None, value["id"].upper() + "x"):
        runtime.cleanup_workers(unsafe)
    assert neighbour.exists()


def test_delegation_tools_are_offered_to_the_lead_coder_only():
    assert DELEGATION_NAMES == {"delegate_tasks", "inspect_worker", "integrate_worker"}
    names = [tool["function"]["name"] for tool in delegation_tools()]
    assert names == ["delegate_tasks", "inspect_worker", "integrate_worker"]
    for role in ("Planner", "Tester", "Reviewer"):
        assert not DELEGATION_NAMES & {tool["function"]["name"] for tool in tools_for(role)}
    # The runtime adds the delegation tools to the Lead Coder, not the base tool list.
    assert "delegate_tasks" not in {tool["function"]["name"] for tool in tools_for("Coder")}


def test_subagent_activity_is_labelled_and_does_not_touch_the_task_file_list(tmp_path):
    async def run():
        _, runtime, value, workspace = harness(tmp_path, WorkerProvider())
        value["files"] = ["AGENTS.md"]
        delegation = Delegation(runtime, value, workspace)
        result = await delegation.call("delegate_tasks", {"tasks": [CORE]}, {})
        # Worker tool calls must be attributable to the subagent, not the Lead Coder.
        labels = {event["agent"] for event in value["events"] if event["text"].startswith("Tool ")}
        assert labels == {"Coder/Core"}
        # The worker wrote in its own copy; the task still lists only integrated files.
        assert value["files"] == ["AGENTS.md"]
        worker = result["workers"][0]
        await delegation.call("inspect_worker", {"worker_id": worker["id"], "path": "core.py"}, {})
        await delegation.call("integrate_worker", {"worker_id": worker["id"]}, {})
        assert value["files"] == ["core.py"]

    asyncio.run(run())
