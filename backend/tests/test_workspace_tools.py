import json
import time

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import Settings
from backend.coordination import HANDOFF_LIMIT
from backend.workspace import Workspace


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
    raise AssertionError("Task did not reach terminal state")


def written(tmp_path, name="main.py", body="line 1\nline 2\n"):
    workspace = Workspace(tmp_path / "workspace")
    workspace.call("write_file", {"path": name, "content": body}, "Coder")
    return workspace


def test_a_range_read_answers_with_numbers_and_its_own_limits(tmp_path):
    """Covers AE3."""
    workspace = written(tmp_path, "big.py", "".join(f"line {number}\n" for number in range(1, 901)))
    result = workspace.call("read_file", {"path": "big.py", "start_line": 100, "end_line": 140}, "Tester")
    assert result["start_line"] == 100 and result["end_line"] == 140
    assert result["total_lines"] == 900
    assert result["partial"] is True
    assert result["truncated"] is False
    assert result["content"].splitlines()[0] == "100\tline 100"
    assert len(result["content"].splitlines()) == 41


def test_a_range_is_capped_and_says_so(tmp_path):
    workspace = written(tmp_path, "big.py", "".join(f"line {number}\n" for number in range(1, 901)))
    result = workspace.call("read_file", {"path": "big.py", "start_line": 10, "end_line": 890}, "Tester")
    assert result["truncated"] is True
    assert result["end_line"] - result["start_line"] + 1 == 400
    assert "end_line + 1" in result["note"]


def test_a_range_past_the_end_names_the_length(tmp_path):
    workspace = written(tmp_path, "small.py", "one\ntwo\n")
    try:
        workspace.call("read_file", {"path": "small.py", "start_line": 40}, "Tester")
    except ValueError as exc:
        assert "2 line(s)" in str(exc)
    else:
        raise AssertionError("a range past the end of the file must be refused")


def test_a_whole_read_is_still_available(tmp_path):
    workspace = written(tmp_path, "small.py", "one\ntwo\n")
    assert workspace.call("read_file", {"path": "small.py"}, "Tester")["content"] == "one\ntwo\n"


def test_python_outline_lists_definitions_without_bodies(tmp_path):
    """Covers AE4."""
    body = (
        "import os\n"
        "from json import loads\n"
        "\n"
        "def alpha(value, scale=1):\n"
        "    secret = 'body text'\n"
        "    return value * scale\n"
        "\n"
        "class Thing:\n"
        "    def method(self):\n"
        "        return 'body text'\n"
    )
    workspace = written(tmp_path, "module.py", body)
    result = workspace.call("file_outline", {"path": "module.py"}, "Tester")
    assert result["kind"] == "parse"
    outline = result["outline"]
    assert "import os" in outline
    assert "json.loads" in outline
    assert "def alpha(value, scale=1)" in outline
    assert "class Thing" in outline
    assert "def method(self)" in outline
    assert "body text" not in outline
    assert outline.splitlines()[0].startswith("1\t")


def test_javascript_outline_is_labelled_a_scan(tmp_path):
    """Covers AE4: a scan never claims to be a parse."""
    body = "import { render } from './render.js';\nexport function draw(scene) {\n  return scene;\n}\n"
    workspace = written(tmp_path, "app.js", body)
    result = workspace.call("file_outline", {"path": "app.js"}, "Tester")
    assert result["kind"] == "scan"
    assert "Line scan, not a parse" in result["note"]
    assert "draw" in result["outline"]
    assert "./render.js" in result["outline"]


def test_a_write_reports_its_own_change(tmp_path):
    workspace = Workspace(tmp_path / "workspace")
    created = workspace.call("write_file", {"path": "main.py", "content": "one\ntwo\n"}, "Coder")
    assert created["created"] is True
    assert created["diff"] == "New file."
    changed = workspace.call("write_file", {"path": "main.py", "content": "one\ntwo changed\n"}, "Coder")
    assert changed["created"] is False
    assert "-two" in changed["diff"] and "+two changed" in changed["diff"]
    edited = workspace.call("edit_file", {"path": "main.py", "old_text": "one\n",
                                          "new_text": "one edited\n"}, "Coder")
    assert "+one edited" in edited["diff"]


def test_validation_is_reused_only_while_the_bytes_are_unchanged(tmp_path):
    """Covers AE5."""
    checks = {}
    workspace = Workspace(tmp_path / "workspace", checks)
    workspace.call("write_file", {"path": "main.py", "content": "print('hello')\n"}, "Coder")
    first = workspace.call("validate_file", {"path": "main.py"}, "Tester")
    assert first["cache"] == "parsed"
    second = workspace.call("validate_file", {"path": "main.py"}, "Tester")
    assert second["cache"] == "reused"
    assert second["check"] == first["check"]
    assert checks["main.py"]["valid"] is True
    workspace.call("write_file", {"path": "main.py", "content": "print('changed')\n"}, "Coder")
    third = workspace.call("validate_file", {"path": "main.py"}, "Tester")
    assert third["cache"] == "parsed"


def test_a_recorded_check_is_not_reused_for_different_bytes(tmp_path):
    checks = {"main.py": {"sha256": "not-the-real-digest", "version": "static-parser-1",
                          "valid": True, "check": "Python syntax only; code was not executed."}}
    workspace = written(tmp_path, "main.py", "print('hello')\n")
    workspace.checks = checks
    assert workspace.call("validate_file", {"path": "main.py"}, "Tester")["cache"] == "parsed"


def test_validation_reports_itself_as_static_after_reuse(tmp_path):
    workspace = written(tmp_path, "main.py", "print('hello')\n")
    workspace.call("validate_file", {"path": "main.py"}, "Tester")
    reused = workspace.call("validate_file", {"path": "main.py"}, "Tester")
    assert "not parsed again" in reused["note"]
    assert "executed" not in reused["note"].lower()


class SnapshotProvider:
    """A Coder that re-reads a file it keeps changing, then finishes."""

    def __init__(self):
        self.snapshots = []

    async def close(self):
        pass

    async def complete(self, model, messages, tools):
        role = next(role for role in ("Planner", "Coder", "Tester", "Reviewer")
                    if f"You are {role}." in messages[0]["content"])
        steps = sum(message["role"] == "tool" for message in messages)
        if role == "Coder":
            if steps == 0:
                return self.call("write_file", {"path": "main.py", "content": "value = 0\n"})
            if steps >= 13:
                return {"content": "Coder complete; value rewritten six times."}
            if steps % 2 == 1:
                return self.call("read_file", {"path": "main.py"})
            return self.call("edit_file", {"path": "main.py",
                                           "old_text": f"value = {steps // 2 - 1}",
                                           "new_text": f"value = {steps // 2}"})
        if len(messages) == 2:
            name, args = {
                "Planner": ("list_files", {}),
                "Tester": ("validate_file", {"path": "main.py"}),
                "Reviewer": ("read_file", {"path": "main.py"}),
            }[role]
            return self.call(name, args)
        text = "Reviewer complete; static checks only."
        if role == "Reviewer":
            return {"content": json.dumps({"verdict": "approved", "summary": text})}
        return {"content": text}

    @staticmethod
    def call(name, args):
        return {"content": None, "tool_calls": [{
            "id": f"{name}-{json.dumps(args, sort_keys=True)}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}]}


def test_reading_a_file_again_after_changing_it_is_not_a_loop(tmp_path):
    """Covers AE6: the read returns new bytes, so the agent is making progress."""
    config = settings(tmp_path, max_tool_rounds=40)
    with TestClient(create_app(config, SnapshotProvider())) as client:
        task_id = client.post("/api/tasks", json={"task": "Edit and re-read"}).json()["id"]
        value = wait_task(client, task_id)
    assert value["status"] == "completed", value.get("error")
    assert (config.workspace_root / task_id / "main.py").read_text() == "value = 6\n"


def test_repeating_one_failing_call_still_stops_the_agent(tmp_path):
    """Covers AE6: an identical failing call is not progress."""
    class FailingProvider(SnapshotProvider):
        async def complete(self, model, messages, tools):
            if "You are Coder." in messages[0]["content"]:
                return self.call("edit_file", {"path": "main.py", "old_text": "not in the file",
                                               "new_text": "anything"})
            return await super().complete(model, messages, tools)

    config = settings(tmp_path, max_model_calls=80)
    with TestClient(create_app(config, FailingProvider())) as client:
        task_id = client.post("/api/tasks", json={"task": "Loop on a failing edit"}).json()["id"]
        value = wait_task(client, task_id)
    assert value["status"] == "failed"
    assert "without reaching a conclusion" in value["error"]
    assert value["model_calls"] < 20, value["model_calls"]


class RecordingProvider:
    """A team whose reports are far longer than the handoff limit allows."""

    def __init__(self, review_outcome=("changes_required", "approved")):
        self.contexts = []
        self.reviews = 0
        self.review_outcome = list(review_outcome)

    async def close(self):
        pass

    async def complete(self, model, messages, tools):
        role = next(role for role in ("Planner", "Coder", "Tester", "Reviewer")
                    if f"You are {role}." in messages[0]["content"])
        if len(messages) == 2:
            self.contexts.append((role, json.loads(messages[1]["content"])))
            if role == "Planner":
                return SnapshotProvider.call("list_files", {})
            if role == "Coder":
                return SnapshotProvider.call("write_file", {"path": "main.py",
                                                            "content": "print('hello')\n"})
            return SnapshotProvider.call("read_file", {"path": "main.py"})
        if role == "Reviewer":
            verdict = self.review_outcome[min(self.reviews, len(self.review_outcome) - 1)]
            self.reviews += 1
            return {"content": json.dumps({"verdict": verdict,
                                           "summary": "R" * (HANDOFF_LIMIT + 500)})}
        return {"content": f"{role} complete; static checks only."}


def test_reports_handed_on_are_bounded_while_the_record_keeps_them(tmp_path):
    """Covers R9: the handoff is bounded, the task record is not."""
    provider = RecordingProvider()
    config = settings(tmp_path, max_repair_rounds=1)
    with TestClient(create_app(config, provider)) as client:
        task_id = client.post("/api/tasks", json={"task": "Write a long report"}).json()["id"]
        value = wait_task(client, task_id)
    assert value["status"] == "completed", value.get("error")
    assert len(value["summary"]) > HANDOFF_LIMIT, "the record must keep the whole report"
    for role, context in provider.contexts:
        for name, text in context["prior_results"].items():
            assert len(text) <= HANDOFF_LIMIT + 100, (role, name, len(text))


def test_a_repair_round_points_at_the_other_reports_instead_of_copying_them(tmp_path):
    """Covers R9: the Coder is told where the Tester's findings already are."""
    provider = RecordingProvider()
    config = settings(tmp_path, max_repair_rounds=1)
    with TestClient(create_app(config, provider)) as client:
        task_id = client.post("/api/tasks", json={"task": "Repair once"}).json()["id"]
        value = wait_task(client, task_id)
    assert value["status"] == "completed", value.get("error")
    assert value["repair_round"] == 1
    coder_contexts = [context for role, context in provider.contexts if role == "Coder"]
    assert len(coder_contexts) == 2, "the Coder must run once for the build and once for the repair"
    repaired = coder_contexts[-1]
    assert set(repaired["repair_feedback"]) == {"review", "note"}
    assert "prior_results" in repaired["repair_feedback"]["note"]
    assert set(repaired["prior_results"]) == {"Planner", "Coder", "Tester", "Reviewer"}
    assert repaired["verification"]["mode"] == "static_only"
