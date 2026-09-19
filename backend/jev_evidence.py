"""Ask JEV whether the finished project satisfies the requirements the task states.

Static parsing proves syntax. It cannot say whether the interface is in Norwegian,
whether one opponent is on the track, or whether the character stays visible. Those
are narrow judgements about an artifact that already exists, which is what JEV answers
in a few hundred milliseconds for a fraction of a cent.

The hard part is not asking. It is refusing to turn an answer into a defect. We
measured JEV answering 0.03-0.3 for questions whose evidence had never been sent, and
the naive reading of that turns "we never showed it the file" into "the product is
broken". So this module separates three things that are easy to conflate:

1. the model's answer, kept raw on every finding;
2. the evidence the answer rests on, recorded per assessment - which files were sent,
   which were left out, which were truncated, and whether anything was executed;
3. the claim we are willing to make, which depends on both and never on the model's
   confidence alone.

The consequences are deliberate and slightly blunt:

* A requirement about behaviour - controls, collisions, visibility, sound, feel - is
  `insufficient_evidence` however confident the answer, because reading source cannot
  establish it in either direction. Only executing or looking can.
* A requirement readable from source is `contradicted` only when the evidence was
  complete. With a relevant file missing or truncated, a low answer stays
  `insufficient_evidence`, so a defect is never established by an answer to a question
  we did not give it the material to answer.
* A value in the middle, or one that is not a probability at all (NaN, infinity, or
  outside 0-1), is never a finding.
* A JEV answer never overrides a failed deterministic check and never approves a task.

Thresholds and the state budget are module constants, and every finding keeps its raw
probability so a calibration pass on our own labelled tasks can move them.
"""

import asyncio
import hashlib
import json
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from .jev import Jev, JevError, is_hosted, noul

ASSESSMENT_VERSION = 1
CONTRADICTED_BELOW = 0.35
SUPPORTED_AT_OR_ABOVE = 0.85
MAX_REQUIREMENTS = 8
# The hosted model accepts 32k tokens of state. Staying near 5k keeps each request
# small and cheap, and we pay per input token.
DEFAULT_STATE_BUDGET = 20_000
TRUNCATION_MARKER = "\n[... truncated for this request ...]"
EVIDENCE_LIMITS_NOTE = (
    "Some project files are missing from or cut short in this request. If a question "
    "depends on text you cannot see, answer as uncertain rather than as refuting it.")

_BULLET = re.compile(r"^\s*(?:[-*\u2022]|\d+[.)])\s+")
_MODAL = re.compile(
    r"\b(must|must not|never|always|only|at least|no |without|include|includes|keep|"
    r"support|should|sikre|m[aå]|aldri|alltid|bare|minst|uten|skal|inkluder|bruk)\b",
    re.IGNORECASE)
# Deliberately over-inclusive. A requirement wrongly called "runtime" costs a missed
# confirmation, which is cheap and visible. One wrongly called "source" costs a
# confident claim about behaviour nobody ran, which is the failure this pass exists
# to prevent.
_RUNTIME = re.compile(
    r"(control|kontroll|keyboard|tastatur|touch|ber[øo]r|tapp|klikk|click|press|knapp|"
    r"button|respond|collision|kollisjon|physics|fysikk|jump|hopp|move|movement|beveg|"
    r"styring|visible|synlig|occlud|dekker|cover|render|frame|fps|smooth|flyt|"
    r"responsive|lag|performance|ytelse|playable|spillbar|plays|feel|fun|morsom|"
    r"sound|audio|lyd|hear|h[øo]r|animat|stuck|fast|fart|speed|poeng|score|"
    r"kollisjon|kj[øo]rbar|tr[åa]kk)", re.IGNORECASE)
# Entry points first, so a one-file game is never crowded out by its own helpers.
_ENTRY_POINTS = ("index.html", "main.py", "app.py", "main.js", "game.js", "src/main.js")


def requirement_lines(task, limit=MAX_REQUIREMENTS):
    """Pick the lines that read as requirements, verbatim and in order.

    Bulleted lines come first, because a task that writes a list is stating its
    contract. Everything else is kept only when it uses a requirement word. This is a
    heuristic for *what to ask about*; it decides nothing about the project.
    """
    bullets, modal = [], []
    for raw in str(task or "").replace("\r", "").split("\n"):
        if not raw.strip():
            continue
        is_bullet = bool(_BULLET.match(raw))
        text = _BULLET.sub("", raw.strip()).strip()
        if not 20 <= len(text) <= 400:
            continue
        if is_bullet:
            bullets.append(text)
        elif _MODAL.search(text):
            modal.append(text)
    ordered = []
    for text in bullets + modal:
        if text not in ordered:
            ordered.append(text)
        if len(ordered) >= limit:
            break
    return ordered


def classify(requirement):
    """Whether reading source could ever settle this requirement.

    "runtime" means the requirement is about what the artifact does when it runs, so
    source cannot confirm or refute it. The marker list is over-inclusive on purpose.
    """
    return "runtime" if _RUNTIME.search(str(requirement)) else "source"


def select_state(root, files, budget=DEFAULT_STATE_BUDGET):
    """Read the files the run wrote and record what did not fit.

    Returns (included, omitted, truncated, characters_used). The truncation marker is
    counted inside the budget: an assessment must not quietly run over the limit it was
    given, which is what appending the marker afterwards did.
    """
    root = Path(root)
    ordered = sorted((name for name in files if name != "AGENTS.md"),
                     key=lambda name: (name not in _ENTRY_POINTS, name))
    included, omitted, truncated, used = [], [], [], 0
    for name in ordered:
        try:
            text = (root / name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            omitted.append(name)
            continue
        remaining = budget - used
        # Below this there is not enough room left for the text to say anything.
        if remaining < 200:
            omitted.append(name)
            continue
        if len(text) > remaining:
            keep = max(0, remaining - len(TRUNCATION_MARKER))
            text = text[:keep] + TRUNCATION_MARKER
            truncated.append(name)
        included.append({"path": name, "text": text})
        used += len(text)
    return included, omitted, truncated, used


def probability(value):
    """The model's number, or None when it is not a probability at all.

    Rejection happens here rather than in the threshold, so NaN, infinity and a value
    outside 0-1 can never be read as a confident answer to anything.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if math.isnan(number) or math.isinf(number) or not 0.0 <= number <= 1.0:
        return None
    return round(number, 4)


def verdict(score, kind, coverage_complete):
    """What we are willing to claim, given the answer and the evidence behind it."""
    if score is None:
        return "invalid"
    if kind == "runtime":
        # Source cannot establish behaviour, in either direction.
        return "insufficient_evidence"
    if score >= SUPPORTED_AT_OR_ABOVE:
        return "supported"
    if score < CONTRADICTED_BELOW:
        return "contradicted" if coverage_complete else "insufficient_evidence"
    return "insufficient_evidence"


def fingerprint(requirements, included, model, coverage_complete, execution_state, budget):
    """A content-based identity for one assessment.

    Reuse is only honest when the questions, the material sent, the model and the
    assessment setup are all the same. Anything else gets a fresh request.
    """
    digest = hashlib.sha256()
    digest.update(f"v{ASSESSMENT_VERSION}|{model}|{budget}|{coverage_complete}|"
                  f"{execution_state}".encode("utf-8"))
    for text in requirements:
        digest.update(b"\x00R")
        digest.update(str(text).encode("utf-8"))
    for item in included:
        digest.update(b"\x00F")
        digest.update(item["path"].encode("utf-8"))
        digest.update(hashlib.sha256(item["text"].encode("utf-8")).digest())
    return digest.hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def cost_of(usage, price_per_mtok):
    """What a request cost, and whether that number was reported or estimated.

    TypeSafe reports tokens, not money, so the number here is an estimate and says so.
    A missing token count is `unknown`, never zero: an unpriced call is not a free one.
    """
    if not isinstance(usage, dict):
        return {"value": None, "kind": "unknown", "basis": "the response reported no usage"}
    tokens = usage.get("input_tokens")
    if isinstance(tokens, bool) or not isinstance(tokens, (int, float)) or tokens < 0:
        return {"value": None, "kind": "unknown",
                "basis": "the response reported no input-token count"}
    return {"value": round(float(tokens) / 1_000_000 * price_per_mtok, 8), "kind": "estimated",
            "basis": f"{int(tokens)} input tokens at ${price_per_mtok}/Mtok; "
                     "the API reports tokens, not money"}


def ledger_add(value, entry):
    """Keep JEV calls in the task's own resource accounting, across repair rounds."""
    ledger = value.setdefault("jev", {
        "calls": [],
        "totals": {"calls": 0, "requests": 0, "reused": 0, "input_tokens": 0,
                   "output_tokens": 0, "unknown_cost_calls": 0,
                   "cost": {"value": 0.0, "kind": "estimated"}}})
    ledger["calls"] = (ledger["calls"] + [entry])[-50:]
    totals = ledger["totals"]
    totals["calls"] += 1
    totals["requests"] += 1 if entry.get("status") == "ran" else 0
    totals["reused"] += 1 if entry.get("reused") else 0
    for field in ("input_tokens", "output_tokens"):
        count = entry.get(field)
        if isinstance(count, (int, float)) and not isinstance(count, bool):
            totals[field] += int(count)
    cost = entry.get("cost") or {}
    if cost.get("kind") == "unknown":
        totals["unknown_cost_calls"] += 1
    elif isinstance(cost.get("value"), (int, float)):
        totals["cost"]["value"] = round(totals["cost"]["value"] + float(cost["value"]), 8)
    if totals["unknown_cost_calls"]:
        totals["cost"]["kind"] = "partial_estimate"
    return ledger


def _outcome(value, status, reason, started, *, model=None, usage=None, price=0.042):
    """Record a pass that sent nothing, so a skip is visible rather than silent."""
    entry = {"purpose": "evidence", "status": status, "reused": False, "model": model,
             "latency_ms": round((time.perf_counter() - started) * 1000),
             "input_tokens": (usage or {}).get("input_tokens"),
             "output_tokens": (usage or {}).get("output_tokens"),
             "cost": {"value": 0.0, "kind": "estimated",
                      "basis": "no request was sent"},
             "at": now()}
    ledger_add(value, entry)
    return {"status": status, "findings": [], "reason": reason, "model": model,
            "assessment_at": now(), "latency_ms": entry["latency_ms"]}


async def collect(settings, value, files, *, client=None, remaining_seconds=None,
                  budget=DEFAULT_STATE_BUDGET):
    """Assess the stated requirements against the finished workspace. Never raises.

    `value` is the task record: a previous assessment is read from it for reuse, and
    every call is appended to its JEV ledger so cost and time stay visible through
    repair rounds. Cancellation of the whole task is the one thing that escapes: a
    decision layer must not swallow the operator's stop.
    """
    started = time.perf_counter()
    if not getattr(settings, "jev_evidence", False):
        return None
    base_url = getattr(settings, "jev_base_url", "") or ""
    if not base_url:
        return None
    api_key = (getattr(settings, "typesafe_api_key", "") or "").strip()
    price = float(getattr(settings, "jev_input_price_per_mtok", 0.042))
    model = getattr(settings, "jev_model", "")
    if is_hosted(base_url) and not api_key:
        # The hosted service needs a key, so no client is created and nothing is sent.
        return _outcome(value, "skipped", "the hosted TypeSafe endpoint needs an API key",
                        started, price=price)
    requirements = requirement_lines(value.get("task"))
    if not requirements:
        return _outcome(value, "skipped", "the task states no line that reads as a requirement",
                        started, price=price)
    included, omitted, truncated, used = select_state(
        Path(settings.workspace_root) / value["id"], files, budget)
    if not included:
        return _outcome(value, "skipped", "the run wrote no readable file to send as evidence",
                        started, price=price)
    coverage_complete = not omitted and not truncated
    execution = (value.get("verification") or {}).get("execution") or {}
    execution_state = str(execution.get("state") or "not_run")
    kinds = {text: classify(text) for text in requirements}
    fingerprint_value = fingerprint(requirements, included, model, coverage_complete,
                                    execution_state, budget)
    evidence = {"included": [item["path"] for item in included], "omitted": omitted,
                "truncated": truncated, "characters": used,
                "coverage": "complete" if coverage_complete else "incomplete",
                "execution_state": execution_state}
    previous = (value.get("verification") or {}).get("jev_evidence") or {}
    if previous.get("status") == "ran" and previous.get("fingerprint") == fingerprint_value:
        # Same questions, same material, same model and setup: reuse the assessment and
        # keep its original time, so a repair round cannot make it look re-decided.
        ledger_add(value, {"purpose": "evidence", "status": "ran", "reused": True,
                           "model": previous.get("model"), "latency_ms": 0,
                           "cost": {"value": 0.0, "kind": "estimated",
                                    "basis": "no request was sent: an identical assessment "
                                             "was reused"},
                           "at": now()})
        return {**previous, "reused": True, "reused_at": now()}
    budget_seconds = max(0.1, float(getattr(settings, "jev_time_budget", 5.0)))
    if remaining_seconds is not None:
        if remaining_seconds <= 1.0:
            return _outcome(value, "skipped", "under a second of the task budget is left",
                            started, price=price)
        budget_seconds = min(budget_seconds, remaining_seconds - 1.0)
    questions = {
        f"r{index}": noul("Does the delivered project satisfy this requirement from the "
                          f"task? Requirement: {text}")
        for index, text in enumerate(requirements)}
    state = {"files": {item["path"]: item["text"] for item in included},
             "evidence_limits": {"omitted": omitted, "truncated": truncated,
                                 "note": EVIDENCE_LIMITS_NOTE}}
    owned = client is None
    try:
        # One budget for the whole pass: retries and the waits between them are inside
        # it, so a slow endpoint cannot hold a finished task open.
        async with asyncio.timeout(budget_seconds):
            if owned:
                client = Jev(api_key=api_key, base_url=base_url, model=model,
                             request_timeout=settings.jev_request_timeout,
                             max_attempts=settings.jev_max_attempts)
            result = await client.system_one(state, questions)
    except TimeoutError:
        return _outcome(value, "unavailable",
                        f"the evidence pass exceeded its {budget_seconds:.1f}s budget",
                        started, model=model, price=price)
    except JevError as exc:
        return _outcome(value, "unavailable", str(exc), started, model=model, price=price)
    except Exception as exc:  # noqa: BLE001 - evidence gathering must never fail a task
        return _outcome(value, "unavailable",
                        f"{type(exc).__name__} while collecting evidence",
                        started, model=model, price=price)
    finally:
        if owned and client is not None:
            try:
                await client.close()
            except Exception:  # noqa: BLE001 - cleanup must not topple the task
                pass
    answers = result.get("answers") or {}
    findings = []
    for index, text in enumerate(requirements):
        score = probability((answers.get(f"r{index}") or {}).get("noul"))
        findings.append({"requirement": text, "kind": kinds[text], "probability": score,
                         "verdict": verdict(score, kinds[text], coverage_complete)})
    usage = result.get("usage")
    ledger_add(value, {"purpose": "evidence", "status": "ran", "reused": False,
                       "model": result.get("resolved_model"),
                       "latency_ms": result.get("latency_ms"),
                       "input_tokens": (usage or {}).get("input_tokens"),
                       "output_tokens": (usage or {}).get("output_tokens"),
                       "cost": cost_of(usage, price), "at": now()})
    return {"status": "ran", "model": result.get("resolved_model"), "reused": False,
            "assessment_at": now(), "fingerprint": fingerprint_value,
            "thresholds": {"contradicted_below": CONTRADICTED_BELOW,
                           "supported_at_or_above": SUPPORTED_AT_OR_ABOVE},
            "findings": findings, "evidence": evidence,
            "usage": usage, "latency_ms": result.get("latency_ms")}
