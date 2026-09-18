"""A minimal MCP server over stdio, so the client can be tested without Blender.

Newline-delimited JSON-RPC 2.0 is the whole protocol: initialize, list the tools,
call one. The tool set here covers the three answers a real server can give — a
result, a reported failure, and silence.
"""

import json
import sys
import time

TOOLS = [
    {"name": "make_cube", "description": "Make a cube.",
     "inputSchema": {"type": "object", "properties": {"size": {"type": "number"}},
                     "required": []}},
    {"name": "fail_loudly", "description": "Report a failure."},
    {"name": "hang", "description": "Never answer."},
]


def send(payload):
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": request_id, "result": {
                "protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake", "version": "1"}}})
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if name == "make_cube":
                send({"jsonrpc": "2.0", "id": request_id, "result": {"content": [
                    {"type": "text", "text": f"made a cube of size {arguments.get('size', 1)}"}]}})
            elif name == "fail_loudly":
                send({"jsonrpc": "2.0", "id": request_id, "result": {
                    "content": [{"type": "text", "text": "the mesh had no faces"}],
                    "isError": True}})
            elif name == "hang":
                time.sleep(120)
            else:
                send({"jsonrpc": "2.0", "id": request_id,
                      "error": {"code": -32601, "message": "no such tool"}})
        else:
            send({"jsonrpc": "2.0", "id": request_id,
                  "error": {"code": -32601, "message": "no such method"}})


if __name__ == "__main__":
    main()
