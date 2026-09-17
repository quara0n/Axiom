"""The single-loop baseline has to be a fair baseline: the same workspace boundary,
the same ledger and the same budgets as the four-role graph, and small enough that
comparing the two arms says something about contexts rather than about tools."""

import asyncio
import json
import uuid

import pytest

from backend.config import Settings
from backend.provider import ProviderError
from backend.runtime import Runtime
from backend.single_loop import INSTRUCTIONS, SingleLoop
from backend.store import Store
from backend.workspace import single_tools, tools_for


def settings(tmp_path, **overrides):
    return Settings(api_key=overrides.pop("api_key", "test-backend-secret"),
                    workspace_root=tmp_path / "workspaces",
                    database=tmp_path / "tasks.sqlite3", **overrides)


def call(name, args, **extra):
    return {**extra, "content": None, "tool_calls": [{
        "id": f"{name}-{uuid.uuid4().hex[:6]}", "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)}}]}


class Scripted:
    """Repeats its last response, so a loop guard is the only thing that can stop it."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def close(self):
        pass

    async def complete(self, model, messages, tools):
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return response


def build(tmp_path, provider):
    config = settings(tmp_path)
    store = Store(config.database)
    value = store.create("Build a thing", "test/model")
    return config, value, Runtime(config, store, provider)


def run(tmp_path, provider):
    config, value, runtime = build(tmp_path, provider)
    return config, value, runtime, asyncio.run(SingleLoop(runtime, value).run())


def test_the_baseline_instructions_stay_pi_class():
    """Pi carried 2 547 characters of instruction text. A baseline that grows our
    full role prompt would stop being a baseline, so this bound is deliberate."""
    assert len(INSTRUCTIONS) < 1500


def test_the_baseline_tool_surface_matches_the_coder():
    """The arms must differ by the number of contexts, not by their tools. If the
    Coder gains a tool, the baseline has to keep up or the comparison is rigged."""
    assert ([tool["function"]["name"] for tool in single_tools()]
            == [tool["function"]["name"] for tool in tools_for("Coder")])


def test_the_single_loop_writes_files_and_reports(tmp_path):
    provider = Scripted([
        call("write_file", {"path": "src/main.js", "content": "export const ok = 1;\n"}),
        {"content": "Wrote src/main.js."},
    ])
    config, value, _, report = run(tmp_path, provider)
    assert report == "Wrote src/main.js."
    written = config.workspace_root / value["id"] / "src" / "main.js"
    assert written.read_text(encoding="utf-8") == "export const ok = 1;\n"
    assert "src/main.js" in value["files"]


def test_the_single_loop_stays_inside_the_workspace(tmp_path):
    provider = Scripted([
        call("write_file", {"path": "../escape.txt", "content": "nope\n"}),
        {"content": "Could not write outside the workspace."},
    ])
    config, value, _, _ = run(tmp_path, provider)
    assert not (config.workspace_root / "escape.txt").exists()
    assert value["tool_failures"] == 1


def test_the_single_loop_records_into_the_same_ledger_as_the_roles(tmp_path):
    provider = Scripted([
        call("list_files", {}, usage={"prompt_tokens": 100, "completion_tokens": 10,
                                      "cost": 0.002,
                                      "prompt_tokens_details": {"cached_tokens": 40}}),
        {"content": "Done.", "usage": {"prompt_tokens": 20, "completion_tokens": 5}},
    ])
    _, value, _, _ = run(tmp_path, provider)
    usage = value["usage"]
    assert usage["by_role"]["Single"]["prompt_tokens"] == 120
    assert usage["by_role"]["Single"]["cached_tokens"] == 40
    assert usage["totals"]["completion_tokens"] == 15
    assert usage["totals"]["cost"] == 0.002
    assert usage["totals"]["calls"] == 2


def test_the_single_loop_gives_up_on_repeated_empty_responses(tmp_path):
    provider = Scripted([{"content": ""}])
    with pytest.raises(ProviderError, match="empty responses"):
        run(tmp_path, provider)
    assert provider.calls == 3


def test_the_single_loop_stops_a_repeated_identical_write(tmp_path):
    provider = Scripted([call("write_file", {"path": "main.py", "content": "print(1)\n"})])
    with pytest.raises(ProviderError, match="repeated"):
        run(tmp_path, provider)
    assert provider.calls == 5


def test_the_single_loop_shares_the_task_model_call_budget(tmp_path):
    config, value, runtime = build(tmp_path, Scripted([{"content": "unused"}]))
    value["model_calls"] = config.max_model_calls
    with pytest.raises(ProviderError, match="model-call budget"):
        asyncio.run(SingleLoop(runtime, value).run())
