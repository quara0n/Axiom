import time

import httpx


class ProviderError(RuntimeError):
    pass


class OpenRouter:
    def __init__(self, api_key: str, max_tokens: int = 4096, reasoning_max_tokens: int = 0,
                 request_timeout: float = 180):
        self.api_key = api_key
        self.max_tokens = max_tokens
        self.reasoning_max_tokens = reasoning_max_tokens
        self.request_timeout = request_timeout
        # A reasoning model with an unbounded budget will think until it runs out of
        # budget instead of acting. Cap it, and drop the field for providers that
        # reject it rather than failing the task.
        self.reasoning_supported = reasoning_max_tokens > 0
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
                                 request_timeout=self.request_timeout)
        previous = self.client
        self.api_key, self.client = replacement.api_key, replacement.client
        await previous.aclose()

    async def _post(self, payload):
        response = await self.client.post("/chat/completions", json=payload)
        attempts = 1
        if response.status_code == 400 and "reasoning" in payload:
            # Some providers reject the reasoning field outright; retry without it.
            self.reasoning_supported = False
            response = await self.client.post("/chat/completions", json={
                key: value for key, value in payload.items() if key != "reasoning"})
            attempts = 2
        return response, attempts

    async def complete(self, model, messages, tools):
        started = time.perf_counter()
        try:
            payload = {
                "model": model, "messages": messages, "tools": tools,
                "tool_choice": "auto", "max_tokens": self.max_tokens,
            }
            if self.reasoning_supported:
                payload["reasoning"] = {"max_tokens": self.reasoning_max_tokens}
            response, attempts = await self._post(payload)
            if response.status_code >= 400:
                # Never return provider bodies, request headers or credentials to browsers.
                raise ProviderError(f"OpenRouter returned HTTP {response.status_code}. Check model, account balance and backend API key.")
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
        except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
            raise ProviderError("OpenRouter request failed or returned an invalid response.") from exc

    async def models(self):
        try:
            response = await self.client.get("/models")
            response.raise_for_status()
            return [{"id": model["id"], "name": model.get("name", model["id"])}
                    for model in response.json()["data"]
                    if "tools" in model.get("supported_parameters", [])]
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise ProviderError("Could not retrieve OpenRouter models.") from exc
