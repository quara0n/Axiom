"""One agent loop instead of four roles: the internal Pi-class baseline.

The four-role graph pays for four separate contexts on one task: four copies of the
project guidance, four tool-schema blocks and a bounded report handed between them.
HarnessTax and OpenBench both measure the harness around a model, and Pi's measured
advantage is that it carries almost none of that. Whether our roles earn their extra
context is a question about our own runtime, and it can only be answered by running
the same task both ways.

This module is that second way: one growing context, a small tool set, the same
Workspace boundary and the same usage ledger. It is a measurement baseline, not a
second product. The dashboard never selects it, and it cannot delegate.
"""

import json
import time

from .coordination import compact_messages
from .control import RepeatGuard, complete_with_limits, trace_tool
from .provider import ProviderError
from .workspace import READ_ONLY_TOOLS, Workspace, single_tools

# The only calls that change the project. Inspection does not count against the
# work-round budget, exactly as in the four-role graph, so the two arms are bounded
# by the same rule rather than by two different ones.
WORK_TOOLS = {"write_file", "edit_file"}

ROLE = "Single"

# Pi carried 2 547 characters of instruction text on its first call. This stays in
# that class on purpose: if the baseline carried our full role prompt it would not
# be a baseline.
INSTRUCTIONS = """You are a coding agent working alone in a confined project workspace.
You have file tools and nothing else: no network, no shell, no secrets, and no access
outside the workspace. Write real, complete files; do not describe files you did not
write. Read before you edit, and prefer a line range or an outline over a whole file.
Inspect what already exists before rebuilding it. Finish with a short report of what
you changed and what you could not verify. Nothing here executes your code: a static
parse is not a runtime test, and you must say so rather than imply otherwise."""


class SingleLoop:
    """One agent, one context, bounded by the task's existing budgets."""

    def __init__(self, runtime, value, workspace=None, model=None):
        self.runtime = runtime
        self.value = value
        self.workspace = workspace or Workspace(
            runtime.settings.workspace_root / value["id"], value.setdefault("checks", {}),
            value.get("constraints"))
        self.model = model or value.get("model") or runtime.settings.model

    def context(self):
        return {"task": self.value["task"], "project_instructions": self.value.get(
            "instructions_snapshot", ""), "constraints": self.workspace.constraints}

    async def run(self):
        value, workspace, runtime = self.value, self.workspace, self.runtime
        tools = single_tools()
        messages = [
            {"role": "system", "content": INSTRUCTIONS},
            {"role": "user", "content": json.dumps(self.context())},
        ]
        nudges = {"empty": 0, "truncated": 0}
        work_rounds = 0
        guard = RepeatGuard()
        recovery = False
        while work_rounds <= runtime.settings.max_tool_rounds:
            if value.get("model_calls", 0) >= runtime.settings.max_model_calls:
                raise ProviderError("Task exceeded its total model-call budget.")
            value["model_calls"] = value.get("model_calls", 0) + 1
            if compact_messages(messages):
                guard.reads.clear()
            try:
                message = await complete_with_limits(runtime.provider, runtime.settings,
                                                     self.model, messages, tools, ROLE, recovery)
            except ProviderError as exc:
                runtime.record_usage(value, ROLE, ROLE, self.model, error=type(exc).__name__)
                raise
            runtime.record_usage(value, ROLE, ROLE, self.model, message)
            if not isinstance(message, dict):
                raise ProviderError("OpenRouter returned an invalid assistant message.")
            calls = message.get("tool_calls") or []
            content = message.get("content")
            if content is not None and not isinstance(content, str):
                raise ProviderError("OpenRouter returned unsupported message content.")
            if not isinstance(calls, list) or len(calls) > 16:
                raise ProviderError("OpenRouter returned too many or invalid tool calls.")
            assistant = {"role": "assistant", "content": content}
            if calls:
                assistant["tool_calls"] = calls
            messages.append(assistant)
            if not calls:
                if message.get("finish_reason") == "length":
                    content = None
                if not content or not content.strip():
                    if message.get("finish_reason") == "length":
                        nudges["truncated"] += 1
                        recovery = True
                        if nudges["truncated"] > 1:
                            raise ProviderError(
                                f"{ROLE} was cut off at the output limit "
                                f"({runtime.settings.max_tokens} tokens) while using "
                                f"{self.model}. Raise AXIOM_MAX_TOKENS or lower "
                                "AXIOM_REASONING_MAX_TOKENS.")
                        messages.append({"role": "user", "content":
                                         "Do not explain or plan. Reply with exactly one tool call now."})
                        continue
                    nudges["empty"] += 1
                    if nudges["empty"] > 2:
                        raise ProviderError(
                            f"{ROLE} returned {nudges['empty']} empty responses in a row while "
                            f"using {self.model}. Choose a different model for this task.")
                    messages.append({"role": "user", "content": (
                        "Your last message was empty. Continue the task: use the workspace "
                        "tools to obtain evidence, and write the required files.")})
                    continue
                return content
            nudges["empty"] = 0
            recovery = False
            for call in calls:
                call_id = call.get("id") if isinstance(call, dict) else None
                if not isinstance(call_id, str):
                    raise ProviderError("OpenRouter returned an invalid tool call ID.")
                name, args = None, {}
                started, result, outcome = time.perf_counter(), None, "failed"
                observed = False
                try:
                    function = call["function"]
                    name = function["name"]
                    if not isinstance(name, str):
                        name = None
                        raise ValueError("Tool name must be a string.")
                    if name in WORK_TOOLS:
                        if work_rounds >= runtime.settings.max_tool_rounds:
                            raise ProviderError(f"{ROLE} exceeded its work round limit.")
                        work_rounds += 1
                    arguments = function["arguments"]
                    if not isinstance(arguments, str) or len(arguments) > 150000:
                        raise ValueError("Tool arguments exceed limit.")
                    args = json.loads(arguments)
                    if not isinstance(args, dict):
                        raise ValueError("Tool arguments must be an object.")
                    warning = False
                    if name not in READ_ONLY_TOOLS:
                        observed = True
                        warning = guard.observe(name, args)
                    if name not in {tool["function"]["name"] for tool in tools}:
                        raise ValueError("This tool is not available to this role.")
                    result = workspace.call(name, args, "Coder")
                    if name in READ_ONLY_TOOLS:
                        observed = True
                        warning = guard.observe(name, args, result, read=True)
                    if warning:
                        result = {**result, "loop_warning": "No new progress after four identical calls. "
                                  "Use the evidence to finish or change your approach."}
                    if name in WORK_TOOLS:
                        value["files"] = workspace.files()
                        if result.get("diff") != "No textual change.":
                            guard.reads.clear()
                    runtime.event(value, ROLE, f"Tool {name}: {args.get('path', 'workspace')}")
                    outcome = "completed"
                except (ValueError, KeyError, TypeError, OSError, SyntaxError) as exc:
                    value["tool_failures"] = value.get("tool_failures", 0) + 1
                    result = {"error": type(exc).__name__,
                              "message": workspace.error_message(exc)}
                    if not observed and name in READ_ONLY_TOOLS:
                        try:
                            guard.observe(name, args, {"error": type(exc).__name__}, read=True)
                        except ValueError:
                            result["message"] = "This read keeps failing. Change the arguments or report the blocker."
                    path = args.get("path") if isinstance(args, dict) else None
                    detail = " ".join(part for part in (name, path)
                                      if isinstance(part, str) and part)
                    runtime.event(value, ROLE,
                                  f"Tool call rejected: {detail or 'invalid call'} "
                                  f"({type(exc).__name__}).")
                finally:
                    trace_tool(value, ROLE, call_id, name, args if isinstance(args, dict) else {},
                               started, result, outcome)
                    runtime.store.save(value)
                messages.append({"role": "tool", "tool_call_id": call_id,
                                 "content": json.dumps(result, ensure_ascii=False)})
        raise ProviderError(
            f"{ROLE} exceeded its work round limit ({runtime.settings.max_tool_rounds}) after "
            "changing the project that many times. Raise AXIOM_MAX_TOOL_ROUNDS or split the task.")
