"""Shared project guidance and context compaction for the local agent team."""

import json
import re

from .workspace import tool_schema

# A report handed to another role is bounded. The full report stays in the task
# record for the operator; sending every role's whole report to every later role is
# how a short repair grows into a long bill.
HANDOFF_LIMIT = 6000


def handoff_text(text, limit=HANDOFF_LIMIT, recipient=None):
    if not isinstance(text, str) or len(text) <= limit:
        return text
    # Extractive, not an invented summary: retain exact source fragments and their
    # offsets. Prioritize defects/constraints even when they occur late in a report.
    terms = {
        "Coder": r"block|defect|fail|must|constraint|acceptance|interface|fix|next",
        "Tester": r"acceptance|changed|file|test|risk|unverified|limit|fail",
        "Reviewer": r"block|defect|fail|evidence|acceptance|unverified|risk|constraint",
    }
    pattern = re.compile(terms.get(recipient, r"block|defect|fail|must|constraint|acceptance|unverified|next"), re.I)
    fragments = []
    offset = 0
    for line in text.splitlines(keepends=True):
        # A huge single line must not monopolize the handoff.
        for start in range(0, len(line), 700):
            body = line[start:start + 700]
            fragments.append((offset + start, body, bool(pattern.search(body))))
        offset += len(line)
    header = "Selected report excerpts (character offsets; not a complete report). "
    footer = "\nUse read_team_report for omitted evidence; full report remains in the task record."
    budget = max(0, limit - len(header) - len(footer))
    picked = {}
    # Preserve beginning, conclusion, then ranked findings across the entire report.
    order = [0, len(fragments) - 1] + sorted(
        range(1, len(fragments) - 1), key=lambda i: not fragments[i][2])
    for i in order:
        if i in picked:
            continue
        offset, body, _ = fragments[i]
        rendered = f"\n[{offset}] {body.rstrip()}"
        if len(rendered) <= budget:
            picked[i] = rendered
            budget -= len(rendered)
    return header + "".join(picked[i] for i in sorted(picked)) + footer


def report_tool():
    return tool_schema("read_team_report", "Read an exact page of a prior team report. "
                       "Reports are claims, not independent verification. Use next_offset for more.",
                       {"role": {"type": "string"}, "offset": {"type": "integer", "minimum": 0}}, ["role"])


def read_team_report(reports, args):
    report = reports.get(args.get("role"))
    if not isinstance(report, str):
        raise ValueError("That report is not available to this role in this round.")
    offset = args.get("offset", 0)
    if not isinstance(offset, int) or isinstance(offset, bool) or not 0 <= offset <= len(report):
        raise ValueError("offset must be a character position within the report.")
    end = min(offset + HANDOFF_LIMIT, len(report))
    return {"role": args["role"], "offset": offset, "content": report[offset:end],
            "total_chars": len(report), "next_offset": end if end < len(report) else None}


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
    changed = False
    for message in messages[2:-6]:
        if size <= limit:
            break
        content = message.get("content") or ""
        if message["role"] == "tool" and len(content) > 500:
            replacement = '{"omitted": true, "message": "Older tool output omitted to save context. Read the file again if needed."}'
            message["content"] = replacement
            changed = True
            size -= len(content) - len(replacement)
        # Full write_file arguments can be larger than their tool responses.
        for call in message.get("tool_calls", []):
            function = call.get("function", {})
            arguments = function.get("arguments", "")
            if size > limit and len(arguments) > 500:
                replacement = json.dumps({"omitted": True, "message": "Earlier tool arguments omitted; inspect current files for their contents."})
                function["arguments"] = replacement
                changed = True
                size -= len(arguments) - len(replacement)
    return changed
