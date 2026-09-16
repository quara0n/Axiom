import json
import time

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import Settings
from backend.runner import declared_checks, execution_environment, run_check, resolve
from backend.workspace import Workspace


def settings(tmp_path, **overrides):
    return Settings(api_key=overrides.pop("api_key", "test-backend-secret"),
                    workspace_root=tmp_path / "workspaces",
                    database=tmp_path / "tasks.sqlite3", **overrides)


def wait_task(client, task_id):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        value = client.get(f"/api/tasks/{task_id}").json()
        if value["status"] in {"completed", "failed", "cancelled"}:
            return value
        time.sleep(0.02)
    raise AssertionError("Task did not reach terminal state")


class ScriptedProvider:
    """Writes a project from the task text and then reports on it."""

    def __init__(self, files):
        self.files = list(files.items())
        self.written = 0

    async def close(self):
        pass

    async def complete(self, model, messages, tools):
        role = next(role for role in ("Planner", "Coder", "Tester", "Reviewer")
                    if f"You are {role}." in messages[0]["content"])
        if role == "Coder":
            if self.written < len(self.files):
                name, content = self.files[self.written]
                self.written += 1
                return self.call("write_file", {"path": name, "content": content})
            return {"content": "Coder complete; the project is written."}
        if len(messages) == 2:
            if role == "Planner":
                return self.call("list_files", {})
            return self.call("list_files", {})
        if role == "Reviewer":
            return {"content": json.dumps({"verdict": "approved", "summary": "Reviewed."})}
        return {"content": f"{role} complete; static checks only."}

    @staticmethod
    def call(name, args):
        return {"content": None, "tool_calls": [{
            "id": f"{name}-{json.dumps(args, sort_keys=True)[:40]}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}]}


PASSING_PROJECT = {
    "package.json": json.dumps({"name": "demo", "scripts": {"test": "node tests/ok.js"}}),
    "tests/ok.js": "console.log('ok');\n",
}

FAILING_PROJECT = {
    "package.json": json.dumps({"name": "demo", "scripts": {"test": "node tests/fail.js"}}),
    "tests/fail.js": "process.exit(3);\n",
}


def test_a_project_without_checks_declares_nothing(tmp_path):
    workspace = Workspace(tmp_path / "workspace")
    workspace.call("write_file", {"path": "main.py", "content": "print('hi')\n"}, "Coder")
    assert declared_checks(workspace) == []


def test_a_package_script_is_discovered_without_running_it(tmp_path):
    workspace = Workspace(tmp_path / "workspace")
    workspace.call("write_file", {"path": "package.json", "content": PASSING_PROJECT["package.json"]}, "Coder")
    checks = declared_checks(workspace)
    assert [check["intent"] for check in checks] == ["tests"]
    assert checks[0]["source"] == "package.json"
    assert checks[0]["declares"] == "node tests/ok.js"
    assert not (tmp_path / "workspace" / "node_modules").exists()


def test_a_python_test_project_is_discovered(tmp_path):
    workspace = Workspace(tmp_path / "workspace")
    workspace.call("write_file", {"path": "pyproject.toml", "content": "[tool.pytest.ini_options]\n"}, "Coder")
    checks = declared_checks(workspace)
    assert [check["intent"] for check in checks] == ["tests"]
    assert checks[0]["source"] == "python"
    assert checks[0]["command"][1:] == ["-m", "pytest", "-q"]


def test_execution_environment_keeps_no_credentials():
    """Covers AE8."""
    environment = execution_environment({
        "PATH": "/usr/bin", "OPENROUTER_API_KEY": "sk-secret", "AXIOM_DATABASE": "tasks.sqlite3",
        "GITHUB_TOKEN": "gh-secret", "MY_PASSWORD": "hunter2", "LANG": "nb_NO.UTF-8"})
    assert environment == {"PATH": "/usr/bin", "LANG": "nb_NO.UTF-8"}


def test_execution_is_disabled_until_the_operator_enables_it(tmp_path):
    """Covers AE7: the checks are reported, and nothing runs."""
    provider = ScriptedProvider(PASSING_PROJECT)
    config = settings(tmp_path)
    with TestClient(create_app(config, provider)) as client:
        task_id = client.post("/api/tasks", json={"task": "Build a demo"}).json()["id"]
        value = wait_task(client, task_id)
    assert value["status"] == "completed", value.get("error")
    execution = value["verification"]["execution"]
    assert execution["state"] == "not_run"
    assert execution["checks"][0]["intent"] == "tests"
    assert "AXIOM_ALLOW_EXECUTION=1" in execution["reason"]
    assert value["verification"]["runtime_tested"] is False
    assert value["verification"]["mode"] == "static_only"
    assert not (config.workspace_root / task_id / "node_modules").exists()


def test_a_failing_declared_check_is_recorded_as_executed_and_failed(tmp_path):
    """Covers AE7."""
    config = settings(tmp_path, allow_execution=True)
    with TestClient(create_app(config, ScriptedProvider(FAILING_PROJECT))) as client:
        task_id = client.post("/api/tasks", json={"task": "Build a failing demo"}).json()["id"]
        value = wait_task(client, task_id)
    execution = value["verification"]["execution"]
    assert execution["state"] == "failed"
    assert isinstance(execution["exit_code"], int) and execution["exit_code"] != 0
    assert execution["timed_out"] is False
    assert execution["intent"] == "tests" and execution["source"] == "package.json"
    assert "npm" in execution["command"] and execution["command"].endswith("test --silent")
    assert execution["checks"][0]["declares"] == "node tests/fail.js"
    assert execution["duration_ms"] >= 0
    assert execution["isolated"] is False
    assert "not an operating-system sandbox" in execution["note"]
    assert value["verification"]["mode"] == "static_and_executed"
    assert value["verification"]["runtime_tested"] is False
    assert any("exit" in event["text"] for event in value["events"])


def test_a_passing_declared_check_marks_the_run_as_executed(tmp_path):
    config = settings(tmp_path, allow_execution=True)
    with TestClient(create_app(config, ScriptedProvider(PASSING_PROJECT))) as client:
        task_id = client.post("/api/tasks", json={"task": "Build a passing demo"}).json()["id"]
        value = wait_task(client, task_id)
    assert value["status"] == "completed", value.get("error")
    assert value["verification"]["execution"]["state"] == "passed"
    assert value["verification"]["runtime_tested"] is True


def test_a_check_that_never_finishes_is_recorded_as_a_timeout(tmp_path):
    workspace = Workspace(tmp_path / "workspace")
    check = {"intent": "tests", "source": "test",
             "command": ["node", "-e", "setTimeout(() => {}, 5000)"], "available": True}
    result = run_check(workspace, check, timeout=0.5)
    assert result["timed_out"] is True
    assert result["exit_code"] is None


def test_a_project_declaring_nothing_reports_no_error(tmp_path):
    config = settings(tmp_path, allow_execution=True)
    project = {"main.py": "print('hi')\n"}
    with TestClient(create_app(config, ScriptedProvider(project))) as client:
        task_id = client.post("/api/tasks", json={"task": "Build a plain project"}).json()["id"]
        value = wait_task(client, task_id)
    execution = value["verification"]["execution"]
    assert execution["state"] == "not_run"
    assert "declares no test or build command" in execution["reason"]


def test_the_runner_runs_the_intent_it_was_asked_for(tmp_path):
    workspace = Workspace(tmp_path / "workspace")
    workspace.call("write_file", {"path": "package.json",
                                  "content": json.dumps({"scripts": {"test": "node -e \"0\""}})}, "Coder")
    check = resolve(workspace, "tests")
    assert check["intent"] == "tests"
    assert resolve(workspace, "build") is None
