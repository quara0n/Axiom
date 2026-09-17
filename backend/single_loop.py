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

import hashlib
import json

from .coordination import compact_messages
from .provider import ProviderError
from .workspace import READ_ONLY_TOOLS, Workspace, single_tools

# Reading many different files is diligence; reading the same one five times is a loop.
MAX_REPEATED_CALLS = 4

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
            runtime.settings.workspace_root / value["id"], value.setdefault("checks", {}))
        self.model = model or value.get("model") or runtime.settings.model

    def context(self):
        return {"task": self.value["task"], "project_instructions": self.value.get(
            "instructions_snapshot", "")}

    async def run(self):
        value, workspace, runtime = self.value, self.workspace, self.runtime
        tools = single_tools()
        messages = [
            {"role": "system", "content": INSTRUCTIONS},
            {"role": "user", "content": json.dumps(self.context())},
        ]
        nudges = {"empty": 0, "truncated": 0}
        work_rounds = 0
        repeats = {}
        while work_rounds <= runtime.settings.max_tool_rounds:
            if value.get("model_calls", 0) >= runtime.settings.max_model_calls:
                raise ProviderError("Task exceeded its total model-call budget.")
            value["model_calls"] = value.get("model_calls", 0) + 1
            compact_messages(messages)
            try:
                message = await runtime.provider.complete(self.model, messages, tools)
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
                if not content or not content.strip():
                    if message.get("finish_reason") == "length":
                        nudges["truncated"] += 1
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
                return content[:24000]
            for call in calls:
                call_id = call.get("id") if isinstance(call, dict) else None
                if not isinstance(call_id, str):
                    raise ProviderError("OpenRouter returned an invalid tool call ID.")
                name, args = None, {}
                try:
                    function = call["function"]
                    name = function["name"]
                    if name in WORK_TOOLS:
                        work_rounds += 1
                    arguments = function["arguments"]
                    if not isinstance(arguments, str) or len(arguments) > 150000:
                        raise ValueError("Tool arguments exceed limit.")
                    args = json.loads(arguments)
                    if not isinstance(args, dict):
                        raise ValueError("Tool arguments must be an object.")
                    signature = f"{name} {args.get('path') or args.get('query') or ''}".strip()
                    argument_digest = hashlib.sha256(
                        json.dumps(args, sort_keys=True).encode()).hexdigest()
                    if name not in READ_ONLY_TOOLS:
                        repeat_key = (name, argument_digest)
                        repeats[repeat_key] = repeats.get(repeat_key, 0) + 1
                        if repeats[repeat_key] > MAX_REPEATED_CALLS:
                            raise ProviderError(
                                f"{ROLE} repeated {signature} without reaching a conclusion. "
                                "Report what you have found so far, or say what is blocking you.")
                    result = workspace.call(name, args, "Coder")
                    if name in READ_ONLY_TOOLS:
                        failed = isinstance(result, dict) and "error" in result
                        material = argument_digest if failed else hashlib.sha256(
                            (argument_digest + json.dumps(result, sort_keys=True,
                                                         ensure_ascii=False)).encode()).hexdigest()
                        repeat_key = (name, material)
                        repeats[repeat_key] = repeats.get(repeat_key, 0) + 1
                        if repeats[repeat_key] > MAX_REPEATED_CALLS:
                            raise ProviderError(
                                f"{ROLE} repeated {signature} without reaching a conclusion: the "
                                f"call returned the same result {repeats[repeat_key]} times and "
                                "the project did not change. Change what you are doing, or report "
                                "what is blocking you.")
                    if name in WORK_TOOLS:
                        value["files"] = workspace.files()
                    runtime.event(value, ROLE, f"Tool {name}: {args.get('path', 'workspace')}")
                except (ValueError, KeyError, TypeError, OSError, SyntaxError) as exc:
                    value["tool_failures"] = value.get("tool_failures", 0) + 1
                    result = {"error": type(exc).__name__,
                              "message": workspace.error_message(exc)}
                    path = args.get("path") if isinstance(args, dict) else None
                    detail = " ".join(part for part in (name, path)
                                      if isinstance(part, str) and part)
                    runtime.event(value, ROLE,
                                  f"Tool call rejected: {detail or 'invalid call'} "
                                  f"({type(exc).__name__}).")
                messages.append({"role": "tool", "tool_call_id": call_id,
                                 "content": json.dumps(result, ensure_ascii=False)})
        raise ProviderError(
            f"{ROLE} exceeded its work round limit ({runtime.settings.max_tool_rounds}) after "
            "changing the project that many times. Raise AXIOM_MAX_TOOL_ROUNDS or split the task.")
