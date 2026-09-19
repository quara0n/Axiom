"""Ask JEV what to do next, and record the answer without acting on it.

The evidence pass recommends nothing. This one does, and shadow mode exists so we can
find out whether the recommendation is any good before it is allowed to change
anything. It never routes, never skips a role, never widens a permission and never
touches a verdict: it writes down what JEV would have said, and what the workflow then
actually did, so the two can be compared later.

It runs between agent rounds and only when the harness has already decided something is
wrong - the repeat guard reported a call that returned no new progress. It is not called
after every tool call, and it builds no second detector: the existing repeat and
progress signals are the trigger.

The options are limited to what the current role and state actually allow. Asking the
harness to run a declared check is a request to the harness, which decides on its own
whether execution is permitted; it is never shell access handed to the Tester.
"""

import asyncio
import time

from .jev import Jev, JevError, choice, is_hosted
from .jev_evidence import (cost_of, ledger_add, now, probability, requirement_lines,
                           select_state)

CONTINUE = "continue_inspection"
RUN_CHECK = "request_runtime_check"
REPAIR = "propose_repair"
FINISH = "finish_with_report"
INSUFFICIENT = "insufficient_basis"

_WRITE_TOOLS = {"write_file", "edit_file", "integrate_worker"}
_INSPECT_TOOLS = {"list_files", "read_file", "search_files", "file_outline",
                  "read_team_report", "inspect_worker", "validate_file"}


def actions_for(role, *, allows_execution, is_coder):
    """The actions that fit this role, state and capability set."""
    options = {
        CONTINUE: "Keep inspecting, but go after material you have not looked at yet.",
        FINISH: "Finish now with a report and the items you could not resolve.",
        INSUFFICIENT: "There is not enough evidence to recommend an action.",
    }
    if allows_execution:
        options[RUN_CHECK] = ("Ask the harness to run a check the project declares. "
                             "The harness decides whether it may; this grants no access.")
    if is_coder:
        options[REPAIR] = "Fix the problem in the workspace now."
    elif role in {"Tester", "Reviewer"}:
        options[REPAIR] = "Report the problem as a defect so the flow can act on it."
    return options


def basis(value, role, *, repeated, rounds, remaining_seconds, allows_execution):
    """What the recommendation rests on: compact, and never file contents.

    Tool payloads are deliberately absent. The trace already stores argument hashes and
    result sizes, and a decision about what to do next does not need the text itself.
    """
    trace = ((value.get("tool_trace") or {}).get("calls") or [])[-6:]
    verification = value.get("verification") or {}
    checked = len(verification.get("checks") or [])
    unsupported = list(verification.get("unsupported_files") or [])
    evidence = verification.get("jev_evidence") or {}
    return {
        "role": role,
        "requirements": requirement_lines(value.get("task"), limit=5),
        "repeated_call_without_progress": bool(repeated),
        "recent_calls": [{"tool": item.get("tool"), "outcome": item.get("outcome"),
                          "result_bytes": item.get("result_bytes")} for item in trace],
        "content_changed_recently": any(
            item.get("tool") in _WRITE_TOOLS and item.get("outcome") == "completed"
            for item in trace),
        "files_parsed": checked,
        "files_not_checkable": unsupported,
        "jev_evidence_coverage": (evidence.get("evidence") or {}).get("coverage"),
        "rounds_used": rounds,
        "seconds_left": None if remaining_seconds is None or remaining_seconds == float("inf")
        else round(float(remaining_seconds)),
        "harness_may_execute": bool(allows_execution),
    }


def classify_next(tool_names, *, finished):
    """What the workflow actually did next, in the same vocabulary as the options."""
    if finished or not tool_names:
        return FINISH
    first = next((name for name in tool_names if name), None)
    if first in _WRITE_TOOLS:
        return REPAIR
    if first in _INSPECT_TOOLS:
        return CONTINUE
    if first and first not in _INSPECT_TOOLS and first not in _WRITE_TOOLS:
        # An outside tool server is the only other capability the harness grants.
        return RUN_CHECK
    return INSUFFICIENT


def _record(value, entry):
    shadow = value.setdefault("jev_shadow", {"assessments": []})
    shadow["assessments"] = (shadow["assessments"] + [entry])[-20:]
    return entry


async def assess(settings, value, role, *, client=None, repeated=False, rounds=0,
                 remaining_seconds=None, allows_execution=False, repair_round=0):
    """One recorded recommendation. Never raises and never changes the workflow.

    Returns the entry it recorded, or None when shadow mode is off, unconfigured or
    already at its per-task cap. Cancellation of the whole task still propagates.
    """
    if not getattr(settings, "jev_shadow", False):
        return None
    base_url = getattr(settings, "jev_base_url", "") or ""
    if not base_url:
        return None
    api_key = (getattr(settings, "typesafe_api_key", "") or "").strip()
    if is_hosted(base_url) and not api_key:
        return None
    if len((value.get("jev_shadow") or {}).get("assessments") or []) >= int(
            getattr(settings, "jev_shadow_max", 3)):
        return None
    started = time.perf_counter()
    price = float(getattr(settings, "jev_input_price_per_mtok", 0.042))
    options = actions_for(role, allows_execution=allows_execution, is_coder=role == "Coder")
    summary = basis(value, role, repeated=repeated, rounds=rounds,
                    remaining_seconds=remaining_seconds, allows_execution=allows_execution)
    budget_seconds = max(0.5, float(getattr(settings, "jev_shadow_timeout", 3.0)))
    if remaining_seconds is not None and remaining_seconds != float("inf"):
        if remaining_seconds <= 1.0:
            return None
        budget_seconds = min(budget_seconds, remaining_seconds - 1.0)
    owned = client is None
    try:
        async with asyncio.timeout(budget_seconds):
            if owned:
                client = Jev(api_key=api_key, base_url=base_url,
                             model=getattr(settings, "jev_model", ""),
                             request_timeout=min(budget_seconds,
                                                 settings.jev_request_timeout),
                             max_attempts=1)
            result = await client.system_one(
                {"situation": summary},
                {"next": choice("What should this agent do next, given the situation?",
                                options)})
    except Exception:  # noqa: BLE001 - a recommendation is optional, never load-bearing
        entry = _record(value, {"at": now(), "role": role, "repair_round": repair_round,
                                "status": "unavailable", "recommendation": None,
                                "options": sorted(options), "basis": summary,
                                "followed": None,
                                "latency_ms": round((time.perf_counter() - started) * 1000)})
        ledger_add(value, {"purpose": "shadow", "status": "unavailable", "reused": False,
                           "model": getattr(settings, "jev_model", ""), "latency_ms": 0,
                           "cost": {"value": 0.0, "kind": "estimated",
                                    "basis": "no request was sent"},
                           "at": now()})
        return entry
    finally:
        if owned and client is not None:
            try:
                await client.close()
            except Exception:  # noqa: BLE001 - cleanup must not topple the task
                pass
    answers = result.get("answers") or {}
    answer = answers.get("next") or {}
    recommendation = answer.get("choice")
    if recommendation not in options:
        recommendation = None
    probabilities = answer.get("probabilities")
    entry = _record(value, {
        "at": now(), "role": role, "repair_round": repair_round, "status": "ran",
        "recommendation": recommendation,
        "probability": (probability(probabilities.get(recommendation))
                        if isinstance(probabilities, dict) and recommendation else None),
        "confidence": answer.get("confidence"), "options": sorted(options),
        "basis": summary, "followed": None, "model": result.get("resolved_model"),
        "latency_ms": result.get("latency_ms")})
    ledger_add(value, {"purpose": "shadow", "status": "ran", "reused": False,
                       "model": result.get("resolved_model"),
                       "latency_ms": result.get("latency_ms"),
                       "input_tokens": (result.get("usage") or {}).get("input_tokens"),
                       "output_tokens": (result.get("usage") or {}).get("output_tokens"),
                       "cost": cost_of(result.get("usage"), price), "at": now()})
    return entry


def note_next_action(value, action):
    """Attach what the workflow actually did to the recommendation it followed."""
    for entry in reversed((value.get("jev_shadow") or {}).get("assessments") or []):
        if entry.get("status") == "ran" and entry.get("followed") is None:
            entry["actual_next_action"] = action
            entry["followed"] = action == entry.get("recommendation")
            entry["resolved_at"] = now()
            return entry
    return None
