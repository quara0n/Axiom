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
        # A reasoning model with an unbounded budget will think until it runs out of
        # budget instead of acting. Cap it, and drop the field for providers that
        # reject it rather than failing the task.
        self.reasoning_unsupported_models = set()
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
        if (response.status_code == 400 and "reasoning" in payload
                and "reasoning" in response.text.lower()):
            # Some providers reject the reasoning field outright; retry without it.
            self.reasoning_unsupported_models.add(payload["model"])
            response = await self.client.post("/chat/completions", json={
                key: value for key, value in payload.items() if key != "reasoning"})
            attempts = 2
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
        if reasoning_limit > 0 and model not in self.reasoning_unsupported_models:
            payload["reasoning"] = {"max_tokens": min(reasoning_limit, output_limit // 2)}
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
                if model in self.reasoning_unsupported_models:
                    payload.pop("reasoning", None)
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
