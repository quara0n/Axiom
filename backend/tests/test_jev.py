"""Offline tests for the JEV client.

No test here touches the network or needs a key: the transport is a mock, and the
fixtures are the documented request and response shapes from
https://docs.typesafe.ai/api.
"""

import asyncio
import json

import httpx
import pytest

from backend.config import Settings
from backend.jev import Jev, JevError, choice, noul, score


ANSWER_BODY = {
    "model": "jev-1.13.0",
    "answers": {
        "is_urgent": {"type": "noul", "noul": 0.92},
        "route": {"type": "choice", "choice": "billing",
                  "probabilities": {"billing": 0.81, "technical": 0.19},
                  "confidence": 0.81},
        "severity": {"type": "score", "score": 1.4,
                     "probabilities": {"0": 0.1, "1": 0.4, "2": 0.5},
                     "confidence": 0.5},
    },
    "usage": {"input_tokens": 312, "output_tokens": 48},
}


def test_the_three_helpers_build_the_documented_question_shapes():
    assert noul("Does it convey urgency?") == {
        "type": "noul", "instructions": "Does it convey urgency?"}
    assert noul("Is it true?", {"true": "yes means x", "false": "no means y"})["criteria"] == {
        "true": "yes means x", "false": "no means y"}
    assert choice("Which team?", {"billing": None})["criteria"] == {"billing": None}
    assert score("How bad?", ["Minor", "Material", "Critical"])["criteria"] == [
        "Minor", "Material", "Critical"]


def test_answers_usage_and_the_answering_model_are_kept():
    async def run():
        jev = Jev()
        await jev.client.aclose()
        jev.client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=ANSWER_BODY)),
            base_url="https://api.typesafe.ai/v1",
        )
        try:
            result = await jev.system_one({"ticket": "charged twice"},
                                          {"is_urgent": noul("Is it urgent?")})
            assert result["answers"]["is_urgent"]["noul"] == 0.92
            assert result["usage"] == {"input_tokens": 312, "output_tokens": 48}
            # The response reports the versioned model, not the alias we sent.
            assert result["resolved_model"] == "jev-1.13.0"
            assert result["attempts"] == 1
        finally:
            await jev.close()
    asyncio.run(run())


def test_a_key_is_sent_as_a_bearer_token_and_an_absent_key_is_not_invented():
    # Assert on the headers the client configures for its own connection, because
    # replacing the transport is a test trick that would otherwise drop them.
    with_key, without_key = Jev(api_key="private-key"), Jev()

    async def close_both():
        await with_key.close()
        await without_key.close()

    asyncio.run(close_both())
    assert with_key.client.headers["authorization"] == "Bearer private-key"
    # A local or free JEV-shaped server needs no credentials at all.
    assert "authorization" not in without_key.client.headers


def test_an_error_never_returns_the_key_or_a_provider_body():
    async def run():
        jev = Jev("private-key")
        await jev.client.aclose()
        jev.client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(401, json={"error": "private-key", "detail": "revoked"})),
            base_url="https://api.typesafe.ai/v1",
        )
        try:
            with pytest.raises(JevError) as caught:
                await jev.system_one("state", {"q": noul("Is this true?")})
            assert "private-key" not in str(caught.value)
            assert "401" in str(caught.value)
        finally:
            await jev.close()
    asyncio.run(run())


def test_a_rate_limit_is_retried():
    calls = {"n": 0}
    payloads = []

    def handler(request):
        calls["n"] += 1
        payloads.append(json.loads(request.content))
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "0"}, json={"error": "slow down"})
        return httpx.Response(200, json=ANSWER_BODY)

    async def run():
        jev = Jev(retry_base=0.0)
        await jev.client.aclose()
        jev.client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://api.typesafe.ai/v1",
        )
        try:
            result = await jev.system_one("state", {"q": noul("Is this true?")})
            assert result["attempts"] == 2
            assert calls["n"] == 2
            assert payloads[0]["model"] == "jev-1.13.0"
            assert payloads[0]["questions"]["q"]["type"] == "noul"
        finally:
            await jev.close()
    asyncio.run(run())


def test_a_request_without_questions_is_refused_before_it_is_sent():
    async def run():
        jev = Jev()
        try:
            with pytest.raises(JevError):
                await jev.system_one("state", {})
        finally:
            await jev.close()
    asyncio.run(run())


def test_jev_configuration_follows_the_same_precedence_as_the_model_key(monkeypatch, tmp_path):
    monkeypatch.setenv("TYPESAFE_API_KEY", "from-the-environment")
    monkeypatch.setenv("AXIOM_JEV_BASE_URL", "http://127.0.0.1:8000/v1")
    monkeypatch.setenv("AXIOM_JEV_MODEL", "test/classifier")
    monkeypatch.setenv("AXIOM_CREDENTIALS", str(tmp_path / "absent.env"))
    config = Settings.from_env()
    assert config.typesafe_api_key == "from-the-environment"
    assert config.jev_base_url == "http://127.0.0.1:8000/v1"
    assert config.jev_model == "test/classifier"
    # Pinned by default: an alias can move under a tuned threshold.
    assert Settings().jev_model == "jev-1.13.0"


def test_the_probe_packs_the_state_by_budget_and_records_what_it_dropped(tmp_path):
    from scripts.jev_probe import build_state

    (tmp_path / "styles.css").write_text("a" * 100, encoding="utf-8")
    (tmp_path / "index.html").write_text("b" * 100, encoding="utf-8")
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")

    state, included = build_state(tmp_path, budget=150)

    # The highest priority file fits, the next one does not, and the small one after
    # it still does. A question whose evidence was dropped is never scored.
    assert included == {"styles.css", "package.json"}
    assert state["files_not_included"] == ["index.html"]
    assert [item["path"] for item in state["file_inventory"]] == [
        "index.html", "package.json", "styles.css"]
