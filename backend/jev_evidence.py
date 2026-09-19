"""Ask JEV whether the finished project satisfies the requirements the task states.

Static parsing proves syntax. It cannot say whether the interface is in Norwegian,
whether one opponent is on the track, or whether the character stays visible. Those
are narrow judgements about an artifact that already exists, which is what JEV is
for and what it answers in a few hundred milliseconds for a fraction of a cent.

Three boundaries keep this honest, and every one of them comes from a failure we
watched in a real run:

- It is evidence, never a verdict. Findings land in the verification record for the
  Tester and Reviewer to weigh. Nothing here can approve a task, and nothing here
  can override a deterministic validation failure.
- Only a confident "no" is a finding. JEV does not abstain: asked without the
  evidence in the state it still answers, and we measured it defaulting to "no" at
  0.03-0.3 rather than admitting it did not know. A probability near 0.5 therefore
  carries no information, so it is recorded as unclear, not as a defect.
- The questions are the task's own sentences and the state is what the run wrote.
  Which files were sent, which were left out and which were truncated is recorded
  beside the answers, because an answer whose evidence was never sent is worthless.

TypeSafe's guidance says a Noul near 0.5 means "similar probability for yes and no",
not medium intensity, and that thresholds must be validated on the workload. The two
thresholds below are deliberately wide for that reason, and every finding keeps its
raw probability so a calibration pass on our own tasks can move them.
"""

import re
from pathlib import Path

from .jev import Jev, JevError, noul

CONTRADICTED_BELOW = 0.35
SUPPORTED_AT_OR_ABOVE = 0.85
MAX_REQUIREMENTS = 8
# The hosted model accepts 32k tokens of state. Staying near 5k keeps each request
# small and cheap, and we pay per input token.
DEFAULT_STATE_BUDGET = 20_000

_BULLET = re.compile(r"^\s*(?:[-*\u2022]|\d+[.)])\s+")
_MODAL = re.compile(
    r"\b(must|must not|never|always|only|at least|no |without|include|includes|keep|"
    r"support|should|sikre|m[aå]|aldri|alltid|bare|minst|uten|skal|inkluder|bruk)\b",
    re.IGNORECASE)
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


def select_state(root, files, budget=DEFAULT_STATE_BUDGET):
    """Read the files the run wrote and record what did not fit.

    Returns (included, omitted, truncated, characters_used). A file that only partly
    fits is truncated and named rather than silently dropped, so the Reviewer can
    tell a wrong answer from one that was never given evidence.
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
            text = text[:remaining] + "\n[... truncated for this request ...]"
            truncated.append(name)
        included.append({"path": name, "text": text})
        used += len(text)
    return included, omitted, truncated, used


def verdict(probability):
    """A confident no is a finding; everything else is honestly unclear.

    JEV answers with a probability, not with confidence in itself, so refusing to act
    on a value near the middle is our job, not the model's.
    """
    if probability < CONTRADICTED_BELOW:
        return "contradicted"
    if probability >= SUPPORTED_AT_OR_ABOVE:
        return "supported"
    return "unclear"


async def collect(settings, task_id, task, files, *, client=None, budget=DEFAULT_STATE_BUDGET):
    """Ask JEV one question per stated requirement. Never raises.

    Returns None when the feature is off or unconfigured, so an operator without a
    TypeSafe key sees exactly the record they saw before. A failure is recorded as
    `unavailable` beside the static checks: gathering evidence must never be the
    reason a task fails.
    """
    owned = client is None
    if owned:
        if not getattr(settings, "jev_evidence", False) or not getattr(settings, "jev_base_url", ""):
            return None
        client = Jev(api_key=settings.typesafe_api_key, base_url=settings.jev_base_url,
                     model=settings.jev_model, request_timeout=settings.jev_request_timeout,
                     max_attempts=settings.jev_max_attempts)
    try:
        requirements = requirement_lines(task)
        if not requirements:
            return {"status": "skipped", "findings": [],
                    "reason": "the task states no line that reads as a requirement"}
        included, omitted, truncated, used = select_state(
            Path(settings.workspace_root) / task_id, files, budget)
        if not included:
            return {"status": "skipped", "findings": [],
                    "reason": "the run wrote no readable file to send as evidence"}
        questions = {
            f"r{index}": noul("Does the delivered project satisfy this requirement from the "
                              f"task? Requirement: {text}")
            for index, text in enumerate(requirements)}
        result = await client.system_one(
            {"files": {item["path"]: item["text"] for item in included}}, questions)
        answers = result.get("answers") or {}
        findings = []
        for index, text in enumerate(requirements):
            probability = (answers.get(f"r{index}") or {}).get("noul")
            if not isinstance(probability, (int, float)) or isinstance(probability, bool):
                findings.append({"requirement": text, "probability": None,
                                 "verdict": "unanswered"})
                continue
            probability = round(float(probability), 4)
            findings.append({"requirement": text, "probability": probability,
                             "verdict": verdict(probability)})
        return {
            "status": "ran",
            "model": result.get("resolved_model"),
            "thresholds": {"contradicted_below": CONTRADICTED_BELOW,
                           "supported_at_or_above": SUPPORTED_AT_OR_ABOVE},
            "findings": findings,
            "evidence": {"included": [item["path"] for item in included], "omitted": omitted,
                         "truncated": truncated, "characters": used},
            "usage": result.get("usage"),
            "latency_ms": result.get("latency_ms"),
        }
    except JevError as exc:
        return {"status": "unavailable", "findings": [], "reason": str(exc)}
    except Exception as exc:  # noqa: BLE001 - evidence gathering must never fail a task
        return {"status": "unavailable", "findings": [],
                "reason": f"{type(exc).__name__} while collecting evidence"}
    finally:
        if owned and client is not None:
            await client.close()
