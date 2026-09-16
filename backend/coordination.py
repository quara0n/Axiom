"""Shared project guidance and context compaction for the local agent team."""

import json

# A report handed to another role is bounded. The full report stays in the task
# record for the operator; sending every role's whole report to every later role is
# how a short repair grows into a long bill.
HANDOFF_LIMIT = 6000


def handoff_text(text, limit=HANDOFF_LIMIT):
    if not isinstance(text, str) or len(text) <= limit:
        return text
    return text[:limit] + "\n... truncated for handoff; the full report is in the task record."


DEFAULT_PROJECT_INSTRUCTIONS = """# Project collaboration

Build the user's requested product, including the setup needed to run it.
Preserve working features when repairing defects. Keep changes focused.

## Ownership and handoffs
Planner owns requirements, architecture, module interfaces and acceptance criteria.
Coder is the only project-code writer and owns integration across modules.
Coder may delegate independent work packages to at most three subagents that write in
isolated copies. Fix the interfaces and file ownership before delegating, then inspect
every proposal and integrate it yourself. Small tasks stay direct.
Tester gathers concrete evidence against the plan. Reviewer decides whether the
requirements are met; distinguish blocking defects from optional improvements.
For every defect, name the file, expected behavior and evidence. Never invent tests.

## Implementation
For larger projects, build a working vertical slice first, then add features and
polish in small steps. Define module interfaces before splitting implementation.
Include dependency manifests and launch instructions when the product needs them.
Avoid placeholders for requested core functionality. Do not rewrite unrelated code.
Inspect cheaply: take a file outline, then the line ranges you need, and read a whole
file only when you need all of it. A write or edit answers with the change it made,
so use that instead of reading the file back.

## Interactive and game projects
Plan the core play loop, controls, camera, collision rules, progression, restart,
visual direction, feedback, audio and performance targets where relevant. Prioritize
a coherent, playable experience before adding breadth. Distinguish implemented
behavior from behavior actually observed in a running application.

## Verification
Static parsing does not prove that imports resolve, an application builds, a scene
renders, controls work or a game feels good. Report runtime and visual checks as
unverified unless actual execution evidence is available. Include reproducible
launch and manual test steps for anything this runtime cannot execute.
"""


def compact_messages(messages, limit=200_000):
    """Drop old tool payloads first, preserving call IDs and recent observations.

    The first two messages contain the durable task/handoff context. An agent can
    re-read any omitted file; trimming never invents a successful tool result.

    The limit has to fit one inspection pass over a real project. When it is too
    tight, an agent that reads a large file gets its own earlier output omitted and
    reads the same file again, which turns into an expensive read loop.
    """
    size = len(json.dumps(messages))
    for message in messages[2:-6]:
        if size <= limit:
            break
        content = message.get("content") or ""
        if message["role"] == "tool" and len(content) > 500:
            replacement = '{"omitted": true, "message": "Older tool output omitted to save context. Read the file again if needed."}'
            message["content"] = replacement
            size -= len(content) - len(replacement)
        # Full write_file arguments can be larger than their tool responses.
        for call in message.get("tool_calls", []):
            function = call.get("function", {})
            arguments = function.get("arguments", "")
            if size > limit and len(arguments) > 500:
                replacement = json.dumps({"omitted": True, "message": "Earlier tool arguments omitted; inspect current files for their contents."})
                function["arguments"] = replacement
                size -= len(arguments) - len(replacement)
