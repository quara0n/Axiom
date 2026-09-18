"""The MCP client, against a fake server that answers, fails and stalls on purpose."""

import asyncio
import json
import sys
import uuid
from pathlib import Path

import pytest

from backend.config import Settings
from backend.mcp import McpError, McpHub, McpServer, parse_servers, split_tool_name, tool_name
from backend.runtime import Runtime
from backend.store import Store

FAKE = Path(__file__).resolve().parent / "fake_mcp_server.py"


def hub(enabled=True, timeout=10.0):
    return McpHub([McpServer("blender", sys.executable, (str(FAKE),))],
                  enabled=enabled, timeout=timeout)


def settings(tmp_path):
    return Settings(api_key="test-backend-secret", workspace_root=tmp_path / "workspaces",
                    database=tmp_path / "tasks.sqlite3", allow_mcp=True)


def call(name, args, **extra):
    return {**extra, "content": None, "tool_calls": [{
        "id": f"{name}-{uuid.uuid4().hex[:6]}", "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)}}]}


def test_servers_come_from_json_or_are_refused():
    servers = parse_servers('[{"name":"blender","command":"uvx","args":["blender-mcp"]}]')
    assert servers[0].name == "blender" and servers[0].args == ("blender-mcp",)
    assert parse_servers("") == []
    for bad in ('{"name":"x"}', '[{"command":"uvx"}]', '[{"name":"x","command":"y","args":[1]}]'):
        with pytest.raises(McpError):
            parse_servers(bad)


def test_a_server_tool_gets_a_name_that_cannot_collide_with_a_workspace_tool():
    assert tool_name("blender", "make cube") == "blender__make_cube"
    assert split_tool_name("blender__make_cube") == ("blender", "make_cube")
    assert split_tool_name("read_file") == (None, None)


def test_mcp_is_off_until_the_operator_turns_it_on():
    """Spawning a tool server is running someone else's code, so it is opt-in."""
    off = hub(enabled=False)
    assert off.tools() == []
    with pytest.raises(McpError, match="MCP is off"):
        off.call("blender__make_cube", {})


def test_the_servers_tools_are_discovered_with_their_schemas():
    open_hub = hub()
    try:
        names = [tool["function"]["name"] for tool in open_hub.tools()]
        assert "blender__make_cube" in names
        schema = next(tool for tool in open_hub.tools()
                      if tool["function"]["name"] == "blender__make_cube")
        assert schema["function"]["parameters"]["properties"]["size"]["type"] == "number"
        assert schema["type"] == "function"
    finally:
        open_hub.close()


def test_a_tool_call_reaches_the_server_and_comes_back():
    open_hub = hub()
    try:
        result = open_hub.call("blender__make_cube", {"size": 3})
        assert result["text"] == "made a cube of size 3"
        assert result["is_error"] is False
    finally:
        open_hub.close()


def test_a_failure_the_server_reports_is_kept_as_a_failure():
    open_hub = hub()
    try:
        result = open_hub.call("blender__fail_loudly", {})
        assert result["is_error"] is True
        assert "no faces" in result["text"]
    finally:
        open_hub.close()


def test_a_server_that_never_answers_gives_up_instead_of_hanging():
    open_hub = hub(timeout=2.0)
    try:
        with pytest.raises(McpError, match="did not answer"):
            open_hub.call("blender__hang", {})
    finally:
        open_hub.close()


def test_an_unknown_tool_is_refused_before_it_leaves_the_process():
    open_hub = hub()
    try:
        with pytest.raises(McpError, match="No MCP server named"):
            open_hub.call("maya__make_cube", {})
    finally:
        open_hub.close()


class Scripted:
    """Plays the four roles, and has the Coder reach for the server's tool once."""

    def __init__(self):
        self.coder_tools = []
        self.stage = 0

    async def close(self):
        pass

    async def complete(self, model, messages, tools):
        system = messages[0]["content"]
        if "You are Planner." in system:
            if len(messages) == 2:
                return call("list_files", {})
            return {"content": "Plan: write a script."}
        if "You are Coder." in system:
            self.coder_tools = [tool["function"]["name"] for tool in tools]
            if self.stage == 0:
                self.stage = 1
                return call("blender__make_cube", {"size": 2})
            if self.stage == 1:
                self.stage = 2
                return call("write_file", {"path": "make.py", "content": "print('cube')\n"})
            return {"content": "Wrote make.py after asking Blender for a cube."}
        if "You are Tester." in system:
            if len(messages) == 2:
                return call("read_file", {"path": "make.py"})
            return {"content": "Static checks only."}
        if len(messages) == 2:
            return call("read_file", {"path": "make.py"})
        return {"content": json.dumps({"verdict": "approved", "summary": "Reviewed."})}


def test_the_coder_can_reach_a_server_tool_and_it_is_recorded(tmp_path):
    config = settings(tmp_path)
    store = Store(config.database)
    value = store.create("Make a cube", "test/model")
    provider = Scripted()
    open_hub = hub()
    runtime = Runtime(config, store, provider, mcp=open_hub)
    try:
        asyncio.run(runtime.run(value))
    finally:
        open_hub.close()

    assert "blender__make_cube" in provider.coder_tools
    # The server's tools arrive alongside the workspace tools, and only the server's
    # are prefixed.
    prefixed = [name for name in provider.coder_tools if "__" in name]
    assert prefixed and all(name.startswith("blender__") for name in prefixed)
    assert "read_file" in provider.coder_tools
    assert value["mcp_calls"] == [{"tool": "blender__make_cube", "role": "Coder", "ok": True}]
    assert value["status"] == "completed", value.get("error")
