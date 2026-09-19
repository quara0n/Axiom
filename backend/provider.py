import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import random
import time

import httpx

# A rate limit or a server error is worth another try; a rejected key or an empty
# balance is not, and retrying those only delays the message the operator needs.
TRANSIENT_STATUSES = {408, 425, 429, 500, 502, 503, 504}
MAX_BACKOFF_SECONDS = 20.0
# OpenRouter accepts a reasoning budget as a token ceiling, as an effort level, or both.
# A provider that ignores one encoding usually honours the other, so we send both and
# never fall back to no budget at all: an unbounded reasoning model thinks until the
# task's own budget is gone.
REASONING_EFFORT_STEPS = ((512, "low"), (2048, "medium"))


def reasoning_effort_for(limit):
    for cap, name in REASONING_EFFORT_STEPS:
        if limit <= cap:
            return name
    return "high"


class ProviderError(RuntimeError):
    pass


class OpenRouter:
    def __init__(self, api_key: str, max_tokens: int = 4096, reasoning_max_tokens: int = 0,
                 request_timeout: float = 180, max_attempts: int = 3, retry_base: float = 1.0):
        self.api_key = api_key
        self.max_tokens = max_tokens
        self.reasoning_max_tokens = reasoning_max_tokens
        self.request_timeout = request_timeout
        self.max_attempts = max(1, max_attempts)
        self.retry_base = max(0.0, retry_base)
        # Which reasoning encoding each model accepted, learned per model. A model that
        # cannot take a budget at all is an error, not a silent licence to think forever.
        self.reasoning_encoding = {}
        self.client = httpx.AsyncClient(
            base_url="https://openrouter.ai/api/v1",
            timeout=httpx.Timeout(request_timeout, connect=10),
            headers={"Authorization": f"Bearer {api_key}", "X-OpenRouter-Title": "Axiom"},
        )

    async def close(self):
        await self.client.aclose()

    async def configure_key(self, api_key: str):
        replacement = OpenRouter(api_key, max_tokens=self.max_tokens,
                                 reasoning_max_tokens=self.reasoning_max_tokens,
                                 request_timeout=self.request_timeout,
                                 max_attempts=self.max_attempts, retry_base=self.retry_base)
        previous = self.client
        self.api_key, self.client = replacement.api_key, replacement.client
        await previous.aclose()

    async def _post(self, payload):
        response = await self.client.post("/chat/completions", json=payload)
        attempts = 1
        budget = payload.get("reasoning")
        if (response.status_code == 400 and isinstance(budget, dict)
                and "reasoning" in response.text.lower()):
            model = payload.get("model")
            if "max_tokens" not in budget:
                # It will not take a reasoning budget in any encoding. Say so, rather than
                # retrying without one and letting the call think for minutes.
                raise ProviderError(
                    f"{model} does not accept a reasoning budget. Choose a model that does, "
                    "or set AXIOM_REASONING_MAX_TOKENS=0 to allow unbounded reasoning.")
            # The provider refused a token ceiling; ask for the same intent with effort,
            # the other documented encoding, and remember which one this model accepted.
            self.reasoning_encoding[model] = "effort"
            retry = {**payload, "reasoning": {"effort": budget.get("effort", "low")}}
            response = await self.client.post("/chat/completions", json=retry)
            attempts = 2
            if response.status_code == 400 and "reasoning" in response.text.lower():
                raise ProviderError(
                    f"{model} does not accept a reasoning budget. Choose a model that does, "
                    "or set AXIOM_REASONING_MAX_TOKENS=0 to allow unbounded reasoning.")
        return response, attempts

    async def complete(self, model, messages, tools):
        return await self.complete_with_policy(model, messages, tools)

    async def complete_with_policy(self, model, messages, tools, *, max_tokens=None,
                                   reasoning_max_tokens=None):
        """Per-call limits must not mutate a provider shared by concurrent agents."""
        started = time.perf_counter()
        output_limit = min(self.max_tokens, max_tokens or self.max_tokens)
        payload = {
            "model": model, "messages": messages, "tools": tools,
            "tool_choice": "auto", "max_tokens": output_limit,
        }
        if not tools:
            payload.pop("tools")
            payload.pop("tool_choice")
        reasoning_limit = (self.reasoning_max_tokens if reasoning_max_tokens is None
                           else reasoning_max_tokens)
        bounded = 0
        if reasoning_limit > 0:
            bounded = max(1, min(reasoning_limit, output_limit // 2))
            effort = reasoning_effort_for(bounded)
            if self.reasoning_encoding.get(model) == "effort":
                payload["reasoning"] = {"effort": effort}
            else:
                payload["reasoning"] = {"max_tokens": bounded, "effort": effort}
        attempts = 0
        try:
            for attempt in range(1, self.max_attempts + 1):
                try:
                    response, posts = await self._post(payload)
                except httpx.HTTPError as exc:
                    attempts += 1
                    if attempt == self.max_attempts:
                        raise ProviderError(
                            f"OpenRouter did not answer after {attempts} attempt(s): {type(exc).__name__}."
                        ) from exc
                    await asyncio.sleep(self._backoff(attempt))
                    continue
                attempts += posts
                if response.status_code in TRANSIENT_STATUSES and attempt < self.max_attempts:
                    await asyncio.sleep(self._backoff(attempt, response.headers.get("retry-after")))
                    continue
                if response.status_code >= 400:
                    # Never return provider bodies, request headers or credentials to browsers.
                    raise ProviderError(f"OpenRouter returned HTTP {response.status_code}. Check model, account balance and backend API key.")
                break
            body = response.json()
            choice = body["choices"][0]
            # Keep the stop reason: an empty message caused by "length" is a truncation,
            # not an unhelpful model, and the caller has to be able to tell them apart.
            message = choice["message"]
            message["finish_reason"] = choice.get("finish_reason")
            # Keep what the provider already reported about the call. The ledger is
            # the only place these numbers exist, and nothing estimates a missing one.
            usage = body.get("usage")
            message["usage"] = usage if isinstance(usage, dict) else None
            # Say what the budget was and whether the provider respected it. A cap that is
            # sent but exceeded is worth knowing about; a cap that is silently dropped is
            # how a Planner ends up thinking for two and a half minutes.
            if bounded:
                details = (usage or {}).get("completion_tokens_details") or {}
                used = details.get("reasoning_tokens")
                message["reasoning_limit"] = bounded
                message["reasoning_encoding"] = ("effort"
                                                 if self.reasoning_encoding.get(model) == "effort"
                                                 else "max_tokens")
                message["reasoning_limit_exceeded"] = (
                    isinstance(used, (int, float)) and not isinstance(used, bool)
                    and used > bounded)
            message["resolved_model"] = body.get("model") if isinstance(body.get("model"), str) else None
            message["generation_id"] = body.get("id") if isinstance(body.get("id"), str) else None
            message["attempts"] = attempts
            message["latency_ms"] = round((time.perf_counter() - started) * 1000)
            return message
        except ProviderError:
            raise
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("OpenRouter request failed or returned an invalid response.") from exc

    def _backoff(self, attempt, retry_after=None):
        """Exponential backoff with jitter, bounded, honouring a rate-limit hint."""
        delay = min(self.retry_base * (2 ** (attempt - 1)), MAX_BACKOFF_SECONDS)
        try:
            hint = float(retry_after)
        except (TypeError, ValueError):
            try:
                hint = (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                hint = None
        if hint is not None and math.isfinite(hint) and hint > 0:
            # Never jitter below the provider's minimum. If its requested wait is
            # outside our bounded retry policy, stop instead of retrying too soon.
            if hint > MAX_BACKOFF_SECONDS:
                raise ProviderError("Provider Retry-After exceeds the bounded retry window. Resume later.")
            return hint
        return delay * (0.5 + random.random() / 2)

    async def models(self):
        try:
            response = await self.client.get("/models")
            response.raise_for_status()
            return [{"id": model["id"], "name": model.get("name", model["id"])}
                    for model in response.json()["data"]
                    if "tools" in model.get("supported_parameters", [])]
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise ProviderError("Could not retrieve OpenRouter models.") from exc
