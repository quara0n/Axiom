"""Fault-injection tests for completion truth, recovery and capability boundaries.

All model replies are scripted. These prove harness behavior, not model quality.
"""

import asyncio
import hashlib
import json
import time

from fastapi.testclient import TestClient
import httpx
import pytest

from backend.app import create_app
from backend.constraints import normalize_constraints
from backend.control import complete_with_limits, trace_tool
from backend.coordination import handoff_text, read_team_report
from backend.delegation import Delegation, OwnedWorkspace
from backend.provider import OpenRouter, ProviderError
from backend.runtime import Runtime
from backend.single_loop import SingleLoop
from backend.store import Store
from backend.tests.test_backend import MockProvider, settings, wait_task
from backend.tests.test_delegation import CORE, WorkerProvider, TeamProvider
from backend.tests.test_runner import ScriptedProvider, FAILING_PROJECT
from backend.workspace import CHECK_VERSION, Workspace


def call(name, args):
    return {"tool_calls": [{"id": "call", "type": "function", "function": {
        "name": name, "arguments": json.dumps(args)}}]}


def components(tmp_path, provider=None, **overrides):
    config = settings(tmp_path, **overrides)
    store = Store(config.database)
    value = store.create("Build", "test/model")
    runtime = Runtime(config, store, provider)
    workspace = Workspace(config.workspace_root / value["id"])
    return runtime, value, workspace


def test_provider_limits_are_per_call_and_per_model(tmp_path):
    async def run():
        provider = OpenRouter("fake", max_tokens=32768, reasoning_max_tokens=2048)
        await provider.client.aclose()
        requests = []

        def respond(request):
            payload = json.loads(request.content)
            requests.append(payload)
            # A provider that refuses the token ceiling but honours the effort level: the
            # case the fallback exists for. One that refuses both is an error, not a
            # licence to run unbounded.
            refused = (payload["model"] == "reject/reasoning"
                       and isinstance(payload.get("reasoning"), dict)
                       and "max_tokens" in payload["reasoning"])
            if refused:
                return httpx.Response(400, json={"error": "reasoning.max_tokens unsupported"})
            return httpx.Response(200, json={"choices": [{"message": {"content": "OK"},
                                                         "finish_reason": "stop"}]})

        provider.client = httpx.AsyncClient(transport=httpx.MockTransport(respond), base_url="https://test.invalid")
        try:
            await provider.complete("reject/reasoning", [], [])
            await asyncio.gather(
                complete_with_limits(provider, settings(tmp_path), "good/model", [], [], "Tester"),
                complete_with_limits(provider, settings(tmp_path), "good/model", [], [], "Coder", True))
        finally:
            await provider.close()
        assert requests[0]["reasoning"]["max_tokens"] == 2048
        # A model that refuses the token ceiling gets the other encoding, never no budget:
        # dropping it silently is how a role ends up reasoning for two and a half minutes.
        assert requests[1]["reasoning"] == {"effort": "medium"}
        assert requests[2]["max_tokens"] == 8192
        assert requests[2]["reasoning"]["max_tokens"] == 2048
        assert requests[3]["max_tokens"] == 4096
        assert requests[3]["reasoning"]["max_tokens"] == 512
        assert provider.max_tokens == 32768
        assert provider.reasoning_max_tokens == 2048
    asyncio.run(run())


@pytest.mark.parametrize("hint", ["3", "19.9"])
def test_retry_after_is_never_shortened_by_jitter(hint):
    provider = object.__new__(OpenRouter)
    provider.retry_base = 1
    assert provider._backoff(1, hint) >= float(hint)
    with pytest.raises(ProviderError, match="Resume later"):
        provider._backoff(1, "120")


def test_runtime_recovery_changes_limits_not_just_the_prompt(tmp_path):
    class Truncated(MockProvider):
        policies = None

        def __init__(self):
            super().__init__()
            self.policies = []

        async def complete_with_policy(self, model, messages, tools, **policy):
            self.policies.append(policy)
            if len(self.policies) == 1:
                return {"content": None, "finish_reason": "length"}
            if len(self.policies) == 2:
                return call("write_file", {"path": "main.py", "content": "value = 1\n"})
            return {"content": "Wrote main.py."}

    provider = Truncated()
    runtime, value, workspace = components(tmp_path, provider)
    assert asyncio.run(runtime.run_agent(value, "Coder", workspace, {})) == "Wrote main.py."
    assert [p["max_tokens"] for p in provider.policies] == [32768, 4096, 32768]
    assert provider.policies[1]["reasoning_max_tokens"] == 512
    assert value["usage"]["calls"][1]["requested_limits"]["recovery"] is True


@pytest.mark.parametrize("arm", ["roles", "single"])
def test_a_warning_allows_the_agent_to_escape_a_read_loop(tmp_path, arm):
    class Reader:
        async def complete(self, model, messages, tools):
            last = messages[-1]
            if last["role"] == "tool" and "loop_warning" in json.loads(last["content"]):
                return {"content": "Read evidence and stopped repeating."}
            return call("read_file", {"path": "main.py"})

    runtime, value, workspace = components(tmp_path, Reader())
    workspace.call("write_file", {"path": "main.py", "content": "x = 1"}, "Coder")
    report = asyncio.run(runtime.run_agent(value, "Planner", workspace, {}) if arm == "roles"
                         else SingleLoop(runtime, value, workspace).run())
    assert report == "Read evidence and stopped repeating."
    assert value["model_calls"] == 5


def test_nonempty_truncated_review_cannot_approve_the_task(tmp_path):
    class TruncatedReview(MockProvider):
        async def complete(self, model, messages, tools):
            if "You are Reviewer." in messages[0]["content"] and len(messages) > 2:
                return {"content": '{"verdict":"approved","summary":"unfinished"}',
                        "finish_reason": "length"}
            return await super().complete(model, messages, tools)

    with TestClient(create_app(settings(tmp_path), TruncatedReview())) as client:
        value = wait_task(client, client.post("/api/tasks", json={"task": "Build"}).json()["id"])
    assert value["status"] == "failed"
    assert value.get("review_verdict") != "approved"


def test_late_blocker_survives_handoff_and_full_report_is_retrievable():
    text = "Background\n" + "Ordinary narrative.\n" * 1000 + "BLOCKER: collision fails in game.js\nConclusion."
    excerpt = handoff_text(text, recipient="Coder")
    assert len(excerpt) <= 6000
    assert "BLOCKER: collision fails" in excerpt
    pages, offset = [], 0
    while offset is not None:
        page = read_team_report({"Tester": text}, {"role": "Tester", "offset": offset})
        pages.append(page["content"])
        offset = page["next_offset"]
    assert "".join(pages) == text
    with pytest.raises(ValueError, match="not available"):
        read_team_report({"Planner": "plan"}, {"role": "Reviewer"})


def test_failing_execution_overrides_an_approving_reviewer(tmp_path):
    config = settings(tmp_path, allow_execution=True, max_repair_rounds=0)
    with TestClient(create_app(config, ScriptedProvider(FAILING_PROJECT))) as client:
        value = wait_task(client, client.post("/api/tasks", json={"task": "Build"}).json()["id"])
    assert value["review_verdict"] == "approved"
    assert value["verification"]["execution"]["state"] == "failed"
    assert value["status"] == "failed"


def test_validation_reparses_even_a_matching_but_corrupt_cache(tmp_path):
    runtime, value, workspace = components(tmp_path)
    source = "def invalid("
    workspace.call("write_file", {"path": "main.py", "content": source}, "Coder")
    value["checks"] = {"main.py": {"valid": True, "version": CHECK_VERSION,
                                  "parsed_at": "2026-09-19T00:00:00+00:00",
                                  "sha256": hashlib.sha256(source.encode()).hexdigest()}}
    asyncio.run(runtime.validate({"value": value, "previous": {}}))
    assert value["validation_errors"] == ["main.py"]
    assert runtime.route_after_validation({"value": value}) == "inspect"


def test_cache_preserves_provenance_of_the_actual_parse(tmp_path):
    workspace = Workspace(tmp_path / "workspace")
    workspace.call("write_file", {"path": "main.py", "content": "x = 1"}, "Coder")
    fresh = workspace.call("validate_file", {"path": "main.py"}, "Tester")
    reused = workspace.call("validate_file", {"path": "main.py"}, "Reviewer")
    assert reused["cache"] == "reused"
    assert reused["sha256"] == fresh["sha256"]
    assert reused["parsed_at"] == fresh["parsed_at"]


def test_executed_rewrite_is_parsed_again_before_approval(tmp_path, monkeypatch):
    runtime, value, workspace = components(tmp_path)
    workspace.call("write_file", {"path": "main.py", "content": "x = 1"}, "Coder")
    state = {"value": value, "previous": {}}
    asyncio.run(runtime.validate(state))

    def rewrite(workspace, settings):
        workspace.path("main.py").write_text("def broken(")
        return {"state": "passed", "intent": "tests", "changed_project": True,
                "exit_code": 0, "duration_ms": 1, "command": "declared check"}

    monkeypatch.setattr("backend.runner.execution_outcome", rewrite)
    asyncio.run(runtime.execute(state))
    assert value["validation_errors"] == ["main.py"]
    assert value["verification"]["execution"]["state"] == "passed"


def test_successful_build_is_not_reported_as_runtime_tests(tmp_path, monkeypatch):
    runtime, value, workspace = components(tmp_path)
    state = {"value": value, "previous": {}}
    asyncio.run(runtime.validate(state))
    monkeypatch.setattr("backend.runner.execution_outcome", lambda *_: {
        "state": "passed", "intent": "build", "changed_project": False,
        "exit_code": 0, "duration_ms": 1, "command": "build"})
    asyncio.run(runtime.execute(state))
    assert value["verification"]["mode"] == "static_and_executed"
    assert value["verification"]["runtime_tested"] is False


class RecordingHub:
    calls = 0

    def available(self):
        return True

    def tools(self):
        return [{"type": "function", "function": {"name": "external__write", "parameters": {}}}]

    def call(self, name, args):
        self.calls += 1
        return {"text": "wrote", "is_error": False, "truncated": False}


@pytest.mark.parametrize("role", ["Planner", "Tester", "Reviewer", "worker", "constrained"])
def test_mcp_dispatch_refuses_forged_calls_from_unprivileged_agents(tmp_path, role):
    class Forged:
        async def complete(self, model, messages, tools):
            return call("external__write", {})

    runtime, value, workspace = components(tmp_path, Forged(), max_model_calls=8)
    hub = runtime.mcp = RecordingHub()
    worker = None
    if role == "worker":
        worker = {"name": "worker", "assignment": "test", "owned_files": ["main.py"],
                  "acceptance_criteria": "test"}
    if role == "constrained":
        workspace.constraints = {"allowed_files": ["main.py"]}
    with pytest.raises(ProviderError):
        asyncio.run(runtime.run_agent(value, "Coder" if role in {"worker", "constrained"} else role,
                                      workspace, {}, worker))
    assert hub.calls == 0


def test_file_constraints_survive_api_continuation_and_enforce_case_and_edits(tmp_path):
    config = settings(tmp_path)
    with TestClient(create_app(config, MockProvider())) as client:
        response = client.post("/api/tasks", json={"task": "Build", "constraints": {
            "allowed_files": ["main.py"], "forbidden_files": ["package.json"]}})
        assert response.status_code == 202
        value = wait_task(client, response.json()["id"])
        assert value["status"] == "completed", value.get("error")
        continued = client.post(f"/api/tasks/{value['id']}/resume").json()
        assert continued["constraints"] == value["constraints"]
        wait_task(client, continued["id"])
        assert client.post("/api/tasks", json={"task": "Bad", "constraints": {
            "allowed_files": ["../main.py"]}}).status_code == 422
    workspace = Workspace(config.workspace_root / value["id"], constraints=value["constraints"])
    with pytest.raises(ValueError, match="constraints"):
        workspace.call("write_file", {"path": "PACKAGE.JSON", "content": "{}"}, "Coder")
    workspace.path("blocked.txt").write_text("old")
    with pytest.raises(ValueError, match="constraints"):
        workspace.call("edit_file", {"path": "blocked.txt", "old_text": "old", "new_text": "new"}, "Coder")
    assert workspace.path("blocked.txt").read_text() == "old"


def test_worker_ownership_cannot_override_task_constraints(tmp_path):
    workspace = OwnedWorkspace(tmp_path / "worker", ["package.json"], {"forbidden_files": ["package.json"]})
    with pytest.raises(ValueError, match="constraints"):
        workspace.call("write_file", {"path": "package.json", "content": "{}"}, "Coder")
    runtime, value, parent = components(tmp_path, WorkerProvider())
    parent.constraints = {"forbidden_files": CORE["owned_files"]}
    with pytest.raises(ValueError, match="constraints"):
        asyncio.run(Delegation(runtime, value, parent).delegate([CORE], {}))
    assert not value.get("workers")


def test_constraints_are_checked_on_output_and_block_unconfined_execution(tmp_path, monkeypatch):
    runtime, value, workspace = components(tmp_path, allow_execution=True)
    workspace.path("package.json").write_text("{}")
    value["constraints"] = {"forbidden_files": ["package.json"]}
    state = {"value": value, "previous": {}}
    asyncio.run(runtime.validate(state))
    assert value["verification"]["constraint_violations"] == ["package.json"]
    monkeypatch.setattr("backend.runner.execution_outcome", lambda *_: pytest.fail("must not execute"))
    asyncio.run(runtime.execute(state))
    assert value["verification"]["execution"]["state"] == "not_run"


def test_worker_tools_follow_actual_proposal_state(tmp_path):
    class Team(TeamProvider):
        def __init__(self):
            super().__init__()
            self.menus = []

        def lead(self, messages, tools):
            self.menus.append({tool["function"]["name"] for tool in tools})
            return super().lead(messages, tools)

    provider = Team()
    runtime, value, _ = components(tmp_path, provider)
    asyncio.run(runtime.run(value))
    assert value["status"] == "completed", value.get("error")
    assert "delegate_tasks" in provider.menus[0]
    assert "inspect_worker" not in provider.menus[0]
    assert "integrate_worker" in provider.menus[1]
    assert "integrate_worker" not in provider.menus[-1]
    assert all(worker["status"] == "integrated" for worker in value["workers"])


def test_trace_identifies_repeated_searches_without_logging_payloads():
    value = {}
    secret = "secret needle never persist"
    for _ in range(305):
        trace_tool(value, "Tester", secret, "search_files", {"query": secret}, time.perf_counter(), {}, "completed")
    trace = value["tool_trace"]
    assert len(trace["calls"]) == 300
    assert trace["total"] == 305
    assert [entry["span"] for entry in trace["calls"]] == list(range(6, 306))
    assert len({entry["arguments_sha256"] for entry in trace["calls"]}) == 1
    assert secret not in json.dumps(trace)
    assert all(entry["duration_ms"] >= 0 for entry in trace["calls"])


def test_usage_sequence_remains_monotonic_after_retention_window(tmp_path):
    runtime, value, _ = components(tmp_path)
    for _ in range(205):
        runtime.record_usage(value, "Tester", "Tester", "test/model")
    assert [call["call"] for call in value["usage"]["calls"]] == list(range(6, 206))


def test_revalidating_new_file_versions_is_progress(tmp_path):
    class Editing:
        step = 0

        async def complete(self, model, messages, tools):
            step, self.step = self.step, self.step + 1
            if step == 12:
                return {"content": "Six versions written and parsed."}
            if step % 2:
                return call("validate_file", {"path": "main.py"})
            return call("write_file", {"path": "main.py", "content": f"x = {step}\n"})

    runtime, value, workspace = components(tmp_path, Editing())
    assert asyncio.run(runtime.run_agent(value, "Coder", workspace, {})) == "Six versions written and parsed."
    assert not value.get("tool_failures")


def test_continuation_can_retrieve_parent_report_without_counting_it_as_fresh_evidence(tmp_path):
    class Reader:
        step = 0

        async def complete(self, model, messages, tools):
            step, self.step = self.step, self.step + 1
            if step == 0:
                assert "Previous/Reviewer" in json.loads(messages[1]["content"])["available_reports"]
                return call("read_team_report", {"role": "Previous/Reviewer", "offset": 6000})
            if step == 1:
                assert "Late finding" in json.loads(messages[-1]["content"])["content"]
                return {"content": "I only read the old report."}
            if step == 2:
                assert "real evidence" in messages[-1]["content"]
                return call("list_files", {})
            return {"content": "Inspected the current workspace as well."}

    runtime, value, workspace = components(tmp_path, Reader())
    parent = runtime.store.create("Before", "test/model")
    parent["agents"][-1]["result"] = "x" * 6000 + "Late finding"
    runtime.store.save(parent)
    value["continue_from"] = parent["id"]
    assert asyncio.run(runtime.run_agent(value, "Planner", workspace, {})).startswith("Inspected")


def test_deadline_warning_preserves_the_hard_limit(tmp_path):
    class Warned:
        step = 0

        async def complete(self, model, messages, tools):
            self.step += 1
            if self.step == 1:
                assert "nearing its time limit" in messages[-1]["content"]
                return call("list_files", {})
            return {"content": "Final handoff."}

    runtime, value, workspace = components(tmp_path, Warned(), task_timeout=100)
    runtime.deadlines[value["id"]] = time.monotonic() + 10
    assert asyncio.run(runtime.run_agent(value, "Planner", workspace, {})) == "Final handoff."
    assert runtime.settings.task_timeout == 100


def test_manual_test_cannot_run_during_a_task_or_ignore_a_file_contract(tmp_path):
    config = settings(tmp_path, allow_execution=True)
    with TestClient(create_app(config, MockProvider())) as client:
        source = client.app.state.store.create("Active", "test/model")
        assert client.post(f"/api/tasks/{source['id']}/test").status_code == 409
        source["status"] = "completed"
        source["constraints"] = {"allowed_files": ["main.py"]}
        client.app.state.store.save(source)
        assert client.post(f"/api/tasks/{source['id']}/test").status_code == 409


def test_manual_failure_revokes_a_prior_completed_status(tmp_path, monkeypatch):
    config = settings(tmp_path, allow_execution=True)
    with TestClient(create_app(config, MockProvider())) as client:
        value = wait_task(client, client.post("/api/tasks", json={"task": "Build"}).json()["id"])
        assert value["status"] == "completed"
        monkeypatch.setattr("backend.runner.execution_outcome", lambda *_: {
            "state": "failed", "intent": "tests", "changed_project": False, "exit_code": 1})
        result = client.post(f"/api/tasks/{value['id']}/test")
        assert result.status_code == 200
        updated = client.get(f"/api/tasks/{value['id']}").json()
        assert updated["status"] == "failed"
        assert updated["verification"]["runtime_tested"] is False


@pytest.mark.parametrize("constraints", [
    [],
    {"unknown": True}, {"allowed_files": "main.py"}, {"allowed_files": ["C:\\secret"]},
    {"allowed_files": ["a"], "forbidden_files": ["A"]},
])
def test_ambiguous_or_invalid_file_contracts_are_rejected(constraints):
    with pytest.raises(ValueError):
        normalize_constraints(constraints)
