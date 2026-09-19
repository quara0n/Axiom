"""Deterministic loop recovery and bounded, operator-facing tool traces."""

import hashlib
import json
import time

from .provider import ProviderError

MAX_REPEATED_CALLS = 4
TRACE_WINDOW = 300
TRACE_ARGUMENT_KEYS = {"path", "query", "start_line", "end_line", "offset", "role",
                       "content", "old_text", "new_text", "tasks", "worker_id", "fresh"}


def argument_digest(args):
    return hashlib.sha256(json.dumps(args, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class RepeatGuard:
    """Allow a corrective turn before stopping; retain hard bounds for real loops.

    Reads are keyed by arguments AND observed content. A compaction may erase that
    evidence, so the caller resets read history when it actually drops payloads.
    Mutations are checked before execution, never replayed as a recovery strategy.
    """

    def __init__(self):
        self.reads = {}
        self.actions = {}

    def observe(self, name, args, result=None, *, read=False):
        if read and name == "validate_file" and isinstance(result, dict):
            result = {key: value for key, value in result.items()
                      if key not in {"parsed_at", "cache", "note"}}
        key = (name, argument_digest(args), argument_digest(result) if read else None)
        counts = self.reads if read else self.actions
        counts[key] = count = counts.get(key, 0) + 1
        if count > MAX_REPEATED_CALLS + 2:
            raise ProviderError(
                f"Agent repeated {name} without reaching a conclusion after corrective feedback.")
        if count > MAX_REPEATED_CALLS:
            raise ValueError(
                "Repeated call made no new progress. Use the evidence already returned, "
                "inspect a different range, change approach, or finish with concrete blockers.")
        return count == MAX_REPEATED_CALLS


def trace_tool(value, label, call_id, name, args, started, result, outcome):
    """Keep comparable argument hashes; never persist source or arbitrary payloads."""
    trace = value.setdefault("tool_trace", {"calls": [], "total": 0})
    trace["total"] += 1
    entry = {
        "span": trace["total"], "agent": label, "tool": name,
        # Provider IDs can contain arbitrary model text. Hash them like arguments.
        "call_id_sha256": argument_digest(call_id),
        "arguments_sha256": argument_digest(args),
        "argument_keys": sorted(key for key in args if key in TRACE_ARGUMENT_KEYS),
        "argument_count": len(args),
        "duration_ms": round((time.perf_counter() - started) * 1000),
        "repair_round": value.get("repair_round", 0), "outcome": outcome,
        "result_bytes": len(json.dumps(result, ensure_ascii=False).encode()) if result is not None else 0,
    }
    # These are bounded, non-content selectors. Search text and write bodies are
    # deliberately represented by the hash, so identical searches remain visible.
    for key in ("start_line", "end_line", "offset"):
        if isinstance(args.get(key), int) and not isinstance(args[key], bool):
            entry[key] = args[key]
    trace["calls"].append(entry)
    trace["calls"] = trace["calls"][-TRACE_WINDOW:]
    return entry


async def complete_with_limits(provider, settings, model, messages, tools, role, recovery=False):
    """Legacy/mock providers keep their interface; real providers enforce ceilings."""
    output = settings.max_tokens
    if role not in {"Coder", "Single"}:
        output = min(output, settings.report_max_tokens)
    reasoning = settings.reasoning_max_tokens
    if recovery:
        output = min(output, 4096)
        reasoning = min(reasoning or 512, 512)
    method = getattr(provider, "complete_with_policy", None)
    if method is None:
        return await provider.complete(model, messages, tools)
    message = await method(model, messages, tools, max_tokens=output,
                           reasoning_max_tokens=reasoning)
    if isinstance(message, dict):
        message["requested_limits"] = {"max_tokens": output, "reasoning_max_tokens": reasoning,
                                       "recovery": recovery}
    return message


def route_tools(tools, delegation, value, settings):
    """Change menus only at capability transitions, preserving stable prefixes.

    File tools stay available: source-file count is a poor proxy for task difficulty.
    Worker tools require an existing proposal; delegation requires remaining quota.
    Dispatch enforces this same selected set, so routing never grants permissions.
    """
    if delegation is None:
        return tools
    unavailable = set()
    if not any(candidate["record"]["status"] == "completed"
               for candidate in delegation.candidates.values()):
        unavailable.update({"inspect_worker", "integrate_worker"})
    if len(value.get("workers") or []) >= settings.max_subagents_per_task:
        unavailable.add("delegate_tasks")
    return [tool for tool in tools if tool["function"]["name"] not in unavailable]
