"""Talk to Model Context Protocol servers.

Axiom's agents work through a fixed set of workspace tools. An MCP server is another
process that offers tools of its own — Blender is the reason this exists — and this
module is the client that reaches it.

The transport is stdio and the wire format is newline-delimited JSON-RPC 2.0, which is
what MCP defines for a locally spawned server: initialize, list the tools, call one.
A server is started once and kept, every call is bounded by a timeout, and the reply
is truncated at a fixed size, because a server that streams a scene dump must not be
able to fill the model's context.

The child gets the same environment treatment as a declared check: everything except
credentials. Spawning a server is still running someone else's code with the backend's
privileges, so a configured server is started only when the operator has turned MCP on
and an agent actually reaches for one of its tools.
"""

import json
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field

from .runner import execution_environment

PROTOCOL_VERSION = "2025-06-18"

# A reply is a hint for the model, not a data channel. Anything longer belongs in a
# file the agent can read on purpose.
MAX_TEXT_CHARS = 8_000
MAX_ERROR_CHARS = 800
START_TIMEOUT = 45.0


class McpError(RuntimeError):
    """A server that cannot be started, does not answer, or answers badly."""


@dataclass(frozen=True)
class McpServer:
    name: str
    command: str
    args: tuple = field(default_factory=tuple)


def parse_servers(raw):
    """Turn the operator's JSON into servers, or raise with a readable reason."""
    if not raw or not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise McpError("AXIOM_MCP_SERVERS is not valid JSON.") from exc
    if not isinstance(data, list):
        raise McpError("AXIOM_MCP_SERVERS must be a JSON array of servers.")
    servers = []
    for entry in data:
        if not isinstance(entry, dict):
            raise McpError("Each MCP server must be a JSON object.")
        name = str(entry.get("name", "")).strip()
        command = str(entry.get("command", "")).strip()
        args = entry.get("args") or []
        if not name or not command:
            raise McpError("Each MCP server needs a name and a command.")
        if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            raise McpError(f"{name}: args must be a list of strings.")
        servers.append(McpServer(name=name, command=command, args=tuple(args)))
    return servers


def tool_name(server_name, tool):
    """Prefix a server's tool so it can never collide with a workspace tool."""
    safe = "".join(character if character.isalnum() or character in "_-" else "_"
                   for character in str(tool))
    return f"{server_name}__{safe}"


def split_tool_name(value):
    name = str(value or "")
    if "__" not in name:
        return None, None
    server, _, tool = name.partition("__")
    return (server or None), (tool or None)


def tool_schema(server_name, tool):
    """An MCP tool as the model's tool schema."""
    parameters = tool.get("inputSchema")
    if not isinstance(parameters, dict):
        parameters = {"type": "object", "properties": {}}
    return {"type": "function", "function": {
        "name": tool_name(server_name, tool.get("name", "")),
        "description": (tool.get("description") or "")[:400],
        "parameters": parameters,
    }}


class McpClient:
    """One stdio server, spoken to one message at a time."""

    def __init__(self, server, timeout=60.0):
        self.server = server
        self.timeout = timeout
        self.process = None
        self.messages = queue.Queue()
        self.next_id = 1
        self._tools = None
        # Re-entrant: start() takes it and then talks to the server through request().
        self._lock = threading.RLock()

    # ---------------------------------------------------------------- lifecycle

    def start(self):
        with self._lock:
            if self.process is not None:
                return
            try:
                self.process = subprocess.Popen(
                    [self.server.command, *self.server.args],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                    env=execution_environment())
            except (OSError, ValueError) as exc:
                raise McpError(f"{self.server.name}: could not start "
                               f"{self.server.command}: {type(exc).__name__}") from exc
        threading.Thread(target=self._read, daemon=True).start()
        self._initialize()

    def _read(self):
        stream = self.process.stdout
        if stream is None:
            return
        for line in stream:
            line = line.strip()
            if not line:
                continue
            try:
                self.messages.put(json.loads(line))
            except ValueError:
                continue

    def stop(self):
        if self.process is None:
            return
        process = self.process
        self.process = None
        try:
            process.terminate()
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass

    # ---------------------------------------------------------------- protocol

    def _write(self, payload):
        if self.process is None or self.process.stdin is None:
            raise McpError(f"{self.server.name} is not running.")
        try:
            self.process.stdin.write(json.dumps(payload) + "\n")
            self.process.stdin.flush()
        except (OSError, ValueError) as exc:
            raise McpError(f"{self.server.name} stopped accepting input.") from exc

    def _await(self, request_id, timeout):
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise McpError(f"{self.server.name} did not answer in "
                               f"{int(timeout)} seconds.")
            try:
                message = self.messages.get(timeout=remaining)
            except queue.Empty:
                raise McpError(f"{self.server.name} did not answer in "
                               f"{int(timeout)} seconds.")
            if not isinstance(message, dict) or message.get("id") != request_id:
                continue
            if "error" in message:
                detail = str(message["error"])[:MAX_ERROR_CHARS]
                raise McpError(f"{self.server.name}: {detail}")
            return message.get("result") or {}

    def request(self, method, params=None, timeout=None):
        self.start()
        with self._lock:
            request_id = self.next_id
            self.next_id += 1
            self._write({"jsonrpc": "2.0", "id": request_id, "method": method,
                         "params": params or {}})
            return self._await(request_id, timeout or self.timeout)

    def notify(self, method, params=None):
        self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _initialize(self):
        self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "axiom", "version": "0.1"},
        }, timeout=START_TIMEOUT)
        self.notify("notifications/initialized")

    # ------------------------------------------------------------------- tools

    def tools(self):
        if self._tools is None:
            result = self.request("tools/list", {}, timeout=START_TIMEOUT)
            listed = result.get("tools")
            self._tools = [tool for tool in listed if isinstance(tool, dict)] \
                if isinstance(listed, list) else []
        return self._tools

    def call(self, name, arguments):
        """Call one tool and hand back text plus whether the server called it a failure."""
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        parts = []
        content = result.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                elif isinstance(item, dict):
                    parts.append(f"[{item.get('type', 'content')}]")
        text = "\n".join(part for part in parts if part).strip()
        if not text:
            text = json.dumps(result)[:MAX_TEXT_CHARS]
        return {"text": text[:MAX_TEXT_CHARS], "truncated": len(text) > MAX_TEXT_CHARS,
                "is_error": bool(result.get("isError"))}


class McpHub:
    """Every configured server, started on first use and kept for the process."""

    def __init__(self, servers, enabled=False, timeout=60.0):
        self.enabled = bool(enabled)
        self.servers = list(servers)
        self.timeout = timeout
        self._clients = {}
        self._tools = None

    @property
    def configured(self):
        return bool(self.servers)

    def available(self):
        return self.enabled and bool(self.servers)

    def client(self, name):
        if name not in {server.name for server in self.servers}:
            raise McpError(f"No MCP server named {name}.")
        if name not in self._clients:
            server = next(server for server in self.servers if server.name == name)
            self._clients[name] = McpClient(server, timeout=self.timeout)
        return self._clients[name]

    def tools(self):
        """The model-facing schemas, or an empty list when MCP is off."""
        if not self.available():
            return []
        if self._tools is None:
            schemas, complete = [], True
            for server in self.servers:
                try:
                    listed = self.client(server.name).tools()
                except McpError:
                    # One server that will not start must not take the run with it.
                    # Nothing is cached, so a later call can try it again.
                    complete = False
                    continue
                schemas.extend(tool_schema(server.name, tool) for tool in listed)
            if complete:
                self._tools = schemas
            return schemas
        return self._tools

    def call(self, name, arguments):
        server_name, tool = split_tool_name(name)
        if not server_name or not tool:
            raise McpError(f"{name} is not an MCP tool.")
        if not self.available():
            raise McpError("MCP is off. Set AXIOM_ALLOW_MCP=1 to use a server's tools.")
        return self.client(server_name).call(tool, arguments if isinstance(arguments, dict) else {})

    def close(self):
        for client in self._clients.values():
            client.stop()
        self._clients = {}
