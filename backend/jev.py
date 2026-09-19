"""TypeSafe System One ("JEV") decisions for Axiom.

JEV is not another chat model. It evaluates a `state` against typed questions and
returns `choice`, `score` and `noul` answers with probabilities and confidence
instead of prose. Two consequences shape this module:

- It is a decision source, not an actor. Nothing here writes a file, grants a
  tool or approves anything, and no threshold lives in this file: callers own
  every side effect and every gate.
- The same request shape is served by TypeSafe's hosted model, by an AI gateway,
  and by the open JEV-shaped servers, so base URL, model and key are
  configuration rather than code. An empty key means "send no Authorization
  header", which is how a local or free server is reached.

TypeSafe's docs and the request/response contract are the source of truth:
https://docs.typesafe.ai/api
"""

import asyncio
import random
import time

import httpx

# Same policy as the OpenRouter client: a rate limit or a server fault is worth
# another try; a rejected key or an over-long state is not, and retrying those
# only delays the message the operator needs.
TRANSIENT_STATUSES = {408, 425, 429, 500, 502, 503, 504}
MAX_BACKOFF_SECONDS = 20.0
DEFAULT_BASE_URL = "https://api.typesafe.ai/v1"
# Pinned rather than jev-latest: an alias can move under a threshold that was
# tuned against a specific version. The response reports which model answered.
DEFAULT_MODEL = "jev-1.13.0"


class JevError(RuntimeError):
    pass


def noul(instructions, criteria=None):
    """A yes/no judgment. Returns the probability that the statement holds."""
    question = {"type": "noul", "instructions": instructions}
    if criteria:
        question["criteria"] = criteria
    return question


def choice(instructions, criteria):
    """One option out of a set the caller names, plus the whole distribution."""
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def score(instructions, criteria):
    """A position on an ordered rubric. Levels run lowest first."""
    return {"type": "score", "instructions": instructions, "criteria": list(criteria)}


class Jev:
    def __init__(self, api_key: str = "", base_url: str = DEFAULT_BASE_URL,
                 model: str = DEFAULT_MODEL, request_timeout: float = 60,
                 max_attempts: int = 3, retry_base: float = 1.0):
        self.api_key = (api_key or "").strip()
        self.base_url = base_url
        self.model = model
        self.request_timeout = request_timeout
        self.max_attempts = max(1, max_attempts)
        self.retry_base = max(0.0, retry_base)
        self.client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(request_timeout, connect=10),
            headers=self._headers(),
        )

    def _headers(self):
        # Keep this minimal. A custom header buys nothing here and adds a way for
        # a proxy to reject the request.
        if self.api_key:
            return {"Authorization": f"Bearer {self.api_key}"}
        return {}

    async def close(self):
        await self.client.aclose()

    async def system_one(self, state, questions, model=None):
        """Evaluate several independent questions against one shared state.

        Questions in a single request run in parallel against the same state and
        cannot see one another's answers, so a request may carry speculative
        questions and the caller keeps the ones that apply.
        """
        if not questions:
            raise JevError("A system one request needs at least one question.")
        payload = {"state": state, "model": model or self.model, "questions": questions}
        started = time.perf_counter()
        attempts = 0
        try:
            for attempt in range(1, self.max_attempts + 1):
                try:
                    response = await self.client.post("/systemone", json=payload)
                except httpx.HTTPError as exc:
                    attempts += 1
                    if attempt == self.max_attempts:
                        raise JevError(
                            f"JEV did not answer after {attempts} attempt(s): {type(exc).__name__}."
                        ) from exc
                    await asyncio.sleep(self._backoff(attempt))
                    continue
                attempts += 1
                if response.status_code in TRANSIENT_STATUSES and attempt < self.max_attempts:
                    await asyncio.sleep(self._backoff(attempt, response.headers.get("retry-after")))
                    continue
                if response.status_code >= 400:
                    # Never return provider bodies, request headers or credentials.
                    raise JevError(
                        f"JEV returned HTTP {response.status_code}. Check the model id, "
                        "the account balance and the API key."
                    )
                break
            body = response.json()
            answers = body["answers"]
            if not isinstance(answers, dict):
                raise JevError("JEV returned no answers.")
            usage = body.get("usage")
            resolved = body.get("model")
            return {
                "answers": answers,
                "usage": usage if isinstance(usage, dict) else None,
                "resolved_model": resolved if isinstance(resolved, str) else None,
                "attempts": attempts,
                "latency_ms": round((time.perf_counter() - started) * 1000),
            }
        except JevError:
            raise
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise JevError("JEV request failed or returned an invalid response.") from exc

    def _backoff(self, attempt, retry_after=None):
        """Exponential backoff with jitter, bounded, honouring a rate-limit hint."""
        delay = min(self.retry_base * (2 ** (attempt - 1)), MAX_BACKOFF_SECONDS)
        try:
            hint = float(retry_after)
        except (TypeError, ValueError):
            hint = None
        if hint is not None and hint > 0:
            delay = min(hint, MAX_BACKOFF_SECONDS)
        return delay * (0.5 + random.random() / 2)
