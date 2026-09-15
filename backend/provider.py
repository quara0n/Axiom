import httpx


class ProviderError(RuntimeError):
    pass


class OpenRouter:
    def __init__(self, api_key: str, max_tokens: int = 4096):
        self.api_key = api_key
        self.max_tokens = max_tokens
        self.client = httpx.AsyncClient(
            base_url="https://openrouter.ai/api/v1", timeout=httpx.Timeout(60, connect=10),
            headers={"Authorization": f"Bearer {api_key}", "X-OpenRouter-Title": "Axiom"},
        )

    async def close(self):
        await self.client.aclose()

    async def configure_key(self, api_key: str):
        replacement = OpenRouter(api_key, max_tokens=self.max_tokens)
        previous = self.client
        self.api_key, self.client = replacement.api_key, replacement.client
        await previous.aclose()

    async def complete(self, model, messages, tools):
        try:
            response = await self.client.post("/chat/completions", json={
                "model": model, "messages": messages, "tools": tools,
                "tool_choice": "auto", "max_tokens": self.max_tokens,
            })
            if response.status_code >= 400:
                # Never return provider bodies, request headers or credentials to browsers.
                raise ProviderError(f"OpenRouter returned HTTP {response.status_code}. Check model, account balance and backend API key.")
            body = response.json()
            choice = body["choices"][0]
            # Keep the stop reason: an empty message caused by "length" is a truncation,
            # not an unhelpful model, and the caller has to be able to tell them apart.
            message = choice["message"]
            message["finish_reason"] = choice.get("finish_reason")
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
