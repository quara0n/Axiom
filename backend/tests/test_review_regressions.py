import asyncio
import json
import shutil
import sqlite3

import pytest

from backend.provider import ProviderError
from backend.runtime import Runtime
from backend.store import Store
from backend.tests.test_backend import settings
from backend.workspace import Workspace


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
@pytest.mark.parametrize(("filename", "source"), [
    ("main.cjs", "export const value = 1;"),
    ("main.mjs", "return 1;"),
    ("index.html", '<script type="module">return 1;</script>'),
])
def test_explicit_module_mode_cannot_fall_back(tmp_path, filename, source):
    workspace = Workspace(tmp_path / "workspace")
    workspace.call("write_file", {"path": filename, "content": source}, "Coder")
    with pytest.raises(ValueError, match="syntax error"):
        workspace.call("validate_file", {"path": filename}, "Tester")


@pytest.mark.parametrize("fail", [False, True])
def test_store_closes_connections_and_preserves_transactions(tmp_path, fail):
    store = Store(tmp_path / "tasks.sqlite3")
    try:
        with store.connect() as connection:
            connection.execute("INSERT INTO tasks VALUES (?, ?)", ("test", "{}"))
            if fail:
                raise ValueError("rollback")
    except ValueError:
        pass
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")
    assert store.get("test") == (None if fail else {})


@pytest.mark.parametrize("batch_size", [1, 4])
def test_work_budget_stops_before_extra_write(tmp_path, batch_size):
    class Writer:
        count = 0

        async def complete(self, model, messages, tools):
            calls = []
            for _ in range(batch_size):
                self.count += 1
                calls.append({"id": str(self.count), "type": "function", "function": {
                    "name": "write_file", "arguments": json.dumps({
                        "path": f"file{self.count}.txt", "content": "data"})}})
            return {"content": None, "tool_calls": calls}

    config = settings(tmp_path, max_tool_rounds=2)
    store = Store(config.database)
    value = store.create("Build", "test/model")
    workspace = Workspace(config.workspace_root / value["id"])
    runtime = Runtime(config, store, Writer())
    with pytest.raises(ProviderError, match="work round limit"):
        asyncio.run(runtime.run_agent(value, "Coder", workspace, {}))
    assert workspace.files() == ["file1.txt", "file2.txt"]
