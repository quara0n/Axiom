import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.tests.test_backend import MockProvider, settings, wait_task
from backend.workspace import MAX_FILE_BYTES, Workspace, copy_workspace_files


class ContextProvider(MockProvider):
    """Records the context every agent receives."""

    def __init__(self):
        super().__init__()
        self.contexts = []

    async def complete(self, model, messages, tools):
        self.contexts.append(json.loads(messages[1]["content"]))
        return await super().complete(model, messages, tools)


def planner_context(provider):
    # The continuation task's Planner is the last one that started with no prior reports.
    return [context for context in provider.contexts if context["prior_results"] == {}][-1]


def test_continuing_a_task_carries_files_reports_and_instructions(tmp_path):
    provider = ContextProvider()
    config = settings(tmp_path)
    with TestClient(create_app(config, provider)) as client:
        first = client.post("/api/tasks", json={
            "task": "Build a racing game", "project_instructions": "Use a chase camera."}).json()
        done = wait_task(client, first["id"])
        assert done["status"] == "completed", done
        assert done["files"] == ["AGENTS.md", "main.py"]

        response = client.post("/api/tasks", json={
            "task": "Add a second track", "continue_from": first["id"]})
        assert response.status_code == 202
        continuation = response.json()
        assert continuation["continue_from"] == first["id"]
        assert continuation["files"] == ["main.py"]
        # The earlier project instructions carry over without being repeated.
        assert continuation["project_instructions"] == "Use a chase camera."
        assert (config.workspace_root / continuation["id"] / "main.py").is_file()

        second = wait_task(client, continuation["id"])
        assert second["status"] == "completed", second
        assert second["files"] == ["AGENTS.md", "main.py"]

    context = planner_context(provider)
    carried = context["continuation"]
    assert carried["task"] == "Build a racing game"
    assert "inspect them before changing anything" in carried["note"]
    assert set(carried["reports"]) == {"Planner", "Coder", "Tester", "Reviewer"}
    assert "Planner complete" in carried["reports"]["Planner"]


def test_continuing_requires_a_finished_task_with_a_workspace(tmp_path):
    provider = MockProvider(delay=0.4)
    with TestClient(create_app(settings(tmp_path), provider)) as client:
        running = client.post("/api/tasks", json={"task": "Build"}).json()
        assert client.post("/api/tasks", json={
            "task": "More", "continue_from": running["id"]}).status_code == 409
        assert client.post("/api/tasks", json={
            "task": "More", "continue_from": "0" * 32}).status_code == 404
        wait_task(client, running["id"])


def test_continuing_inherits_the_earlier_model_plan(tmp_path):
    provider = MockProvider()
    with TestClient(create_app(settings(tmp_path), provider)) as client:
        first = client.post("/api/tasks", json={
            "task": "Build", "model": "plan/main", "models": {"Coder": "plan/coder"}}).json()
        wait_task(client, first["id"])
        # No model in the request: the continuation keeps the team from the source task.
        second = client.post("/api/tasks", json={
            "task": "More", "continue_from": first["id"]}).json()
        assert second["model"] == "plan/main"
        assert second["models"] == {"Coder": "plan/coder"}
        assert second["agents"][1]["model"] == "plan/coder"
        wait_task(client, second["id"])
        # An explicit choice still wins.
        third = client.post("/api/tasks", json={
            "task": "Other", "model": "other/main", "continue_from": first["id"]}).json()
        assert third["model"] == "other/main"
        wait_task(client, third["id"])


def test_continuing_refuses_a_source_that_breaks_the_workspace_limits(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "huge.js").write_bytes(b"x" * (MAX_FILE_BYTES + 1))
    with pytest.raises(ValueError, match="copy limit"):
        copy_workspace_files(source, Workspace(tmp_path / "target"))


def test_continuing_skips_guidance_files(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "AGENTS.md").write_text("Old guidance that must not win.", encoding="utf-8")
    (source / "nested").mkdir()
    (source / "nested" / "game.js").write_text("export const game = 1;\n", encoding="utf-8")
    target = Workspace(tmp_path / "target")
    assert copy_workspace_files(source, target) == ["nested/game.js"]
    assert target.files() == ["nested/game.js"]
    assert Path(target.root, "nested", "game.js").read_text() == "export const game = 1;\n"
