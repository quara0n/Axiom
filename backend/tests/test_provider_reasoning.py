"""The reasoning budget is sent, never silently dropped, and reported honestly."""

import asyncio
import json

import httpx
import pytest

from backend.provider import OpenRouter, ProviderError, reasoning_effort_for


def reply(reasoning_tokens=None, status=200, text=None):
    if status != 200:
        return httpx.Response(status, text=text or '{"error": "reasoning not supported"}')
    details = {} if reasoning_tokens is None else {"reasoning_tokens": reasoning_tokens}
    return httpx.Response(200, json={
        "id": "gen-1", "model": "test/model",
        "choices": [{"message": {"content": "done"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20,
                  "completion_tokens_details": details}})


def provider(handler):
    instance = OpenRouter("k", reasoning_max_tokens=2048)
    asyncio.run(instance.client.aclose())
    instance.client = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                        base_url="https://openrouter.ai/api/v1")
    return instance


def test_the_budget_is_sent_as_a_ceiling_and_an_effort_level():
    payloads = []

    def handler(request):
        payloads.append(json.loads(request.content))
        return reply(reasoning_tokens=100)

    instance = provider(handler)
    message = asyncio.run(instance.complete_with_policy("test/model", [], [],
                                                        max_tokens=8192,
                                                        reasoning_max_tokens=2048))
    asyncio.run(instance.close())
    assert payloads[0]["reasoning"] == {"max_tokens": 2048, "effort": "medium"}
    assert message["reasoning_limit"] == 2048
    assert message["reasoning_encoding"] == "max_tokens"
    assert message["reasoning_limit_exceeded"] is False


def test_a_refused_ceiling_falls_back_to_effort_and_never_to_no_budget():
    payloads = []

    def handler(request):
        payloads.append(json.loads(request.content))
        if len(payloads) == 1:
            return reply(status=400)
        return reply(reasoning_tokens=50)

    instance = provider(handler)
    message = asyncio.run(instance.complete_with_policy("test/model", [], [],
                                                        max_tokens=8192,
                                                        reasoning_max_tokens=2048))
    asyncio.run(instance.close())
    # Every request carried a budget: the fallback is a different encoding, not none.
    assert all("reasoning" in payload for payload in payloads)
    assert payloads[1]["reasoning"] == {"effort": "medium"}
    assert message["reasoning_encoding"] == "effort"


def test_a_model_that_takes_no_budget_at_all_is_an_error():
    def handler(request):
        return reply(status=400)

    instance = provider(handler)
    with pytest.raises(ProviderError) as caught:
        asyncio.run(instance.complete_with_policy("test/model", [], [],
                                                  max_tokens=8192,
                                                  reasoning_max_tokens=2048))
    asyncio.run(instance.close())
    # Silence is the one unacceptable outcome: the operator gets a name and a setting.
    assert "does not accept a reasoning budget" in str(caught.value)
    assert "AXIOM_REASONING_MAX_TOKENS" in str(caught.value)


def test_reported_reasoning_over_the_cap_is_flagged():
    def handler(request):
        return reply(reasoning_tokens=9000)

    instance = provider(handler)
    message = asyncio.run(instance.complete_with_policy("test/model", [], [],
                                                        max_tokens=8192,
                                                        reasoning_max_tokens=2048))
    asyncio.run(instance.close())
    assert message["reasoning_limit"] == 2048
    assert message["reasoning_limit_exceeded"] is True


def test_no_budget_means_no_reasoning_field():
    payloads = []

    def handler(request):
        payloads.append(json.loads(request.content))
        return reply()

    instance = provider(handler)
    asyncio.run(instance.complete_with_policy("test/model", [], [],
                                              max_tokens=8192, reasoning_max_tokens=0))
    asyncio.run(instance.close())
    assert "reasoning" not in payloads[0]


def test_the_effort_ladder_matches_the_ceiling():
    assert reasoning_effort_for(256) == "low"
    assert reasoning_effort_for(2048) == "medium"
    assert reasoning_effort_for(8192) == "high"
