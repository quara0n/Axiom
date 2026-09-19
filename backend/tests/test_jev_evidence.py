"""Offline tests for the JEV evidence pass. No network, no key, no TypeSafe account."""

import asyncio

import pytest

from backend import jev_evidence as evidence
from backend.config import Settings
from backend.jev import JevError
from backend.runtime import Runtime
from backend.store import Store


class FakeJev:
    """A JEV client that answers from a script and records what it was asked."""

    def __init__(self, answers=None, error=None, delay=0.0, usage="default"):
        self.answers = answers or {}
        self.error = error
        self.delay = delay
        self.usage = ({"input_tokens": 10, "output_tokens": 2} if usage == "default"
                      else usage)
        self.requests = []
        self.closed = False

    async def system_one(self, state, questions):
        self.requests.append((state, questions))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return {"answers": self.answers, "usage": self.usage,
                "resolved_model": "jev-test", "attempts": 1, "latency_ms": 5}

    async def close(self):
        self.closed = True


class NeverConstructed:
    """Fails loudly if the pass builds a client it should not have built."""

    created = 0

    def __init__(self, *args, **kwargs):
        NeverConstructed.created += 1
        raise AssertionError("no client may be created here")


def settings(tmp_path, **overrides):
    return Settings(api_key="test", workspace_root=tmp_path / "workspaces",
                    database=tmp_path / "tasks.sqlite3",
                    jev_base_url=overrides.pop("jev_base_url", ""), **overrides)


def task_value(**overrides):
    value = {"id": "task1",
             "task": "- It must contain the word hello.\n"
                     "- The file must never contain the word goodbye."}
    value.update(overrides)
    return value


def project(tmp_path, **extra):
    root = tmp_path / "workspaces" / "task1"
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text("<html></html>", encoding="utf-8")
    for name, body in extra.items():
        (root / name).write_text(body, encoding="utf-8")
    return root


def run(settings_object, value, files, **kwargs):
    return asyncio.run(evidence.collect(settings_object, value, files, **kwargs))


def test_requirements_are_taken_verbatim_with_bullets_first():
    task = ("Build a game.\n"
            "- The character must always be visible on screen.\n"
            "- No losing state, and the player can always retry.\n"
            "This paragraph says nothing useful about the product.\n"
            "It must also support touch controls for a tablet.")
    lines = evidence.requirement_lines(task)
    assert lines[0] == "The character must always be visible on screen."
    assert lines[1] == "No losing state, and the player can always retry."
    assert "It must also support touch controls for a tablet." in lines
    assert "Build a game." not in lines


def test_requirements_are_capped_and_deduplicated():
    task = "\n".join(f"- Requirement number {index} must hold on every screen." for index in range(14))
    task += "\n- Requirement number 0 must hold on every screen."
    lines = evidence.requirement_lines(task)
    assert len(lines) == evidence.MAX_REQUIREMENTS
    assert len(set(lines)) == len(lines)


def test_behaviour_cannot_be_settled_by_reading_source():
    assert evidence.classify("The controls must respond to the keyboard.") == "runtime"
    assert evidence.classify("The touch buttons must not cover the character.") == "runtime"
    assert evidence.classify("The interface text must be Norwegian.") == "source"
    assert evidence.classify("There must be three levels.") == "source"


def test_the_claim_depends_on_the_evidence_and_not_on_confidence():
    # A confident answer about behaviour is still not a confirmation.
    assert evidence.verdict(0.97, "runtime", True) == "insufficient_evidence"
    # A low answer with a file missing stays a question, not a defect.
    assert evidence.verdict(0.02, "source", False) == "insufficient_evidence"
    # Only a low answer over complete evidence is a contradiction, and even then it is a
    # hypothesis for an agent to investigate rather than a proven defect.
    assert evidence.verdict(0.02, "source", True) == "contradicted"
    assert evidence.verdict(0.97, "source", True) == "supported"
    assert evidence.verdict(0.5, "source", True) == "insufficient_evidence"
    assert evidence.verdict(None, "source", True) == "invalid"


def test_a_value_that_is_not_a_probability_is_rejected():
    assert evidence.probability(float("nan")) is None
    assert evidence.probability(float("inf")) is None
    assert evidence.probability(-0.01) is None
    assert evidence.probability(1.01) is None
    assert evidence.probability(True) is None
    assert evidence.probability("0.9") is None
    assert evidence.probability(None) is None
    assert evidence.probability(0.0) == 0.0
    assert evidence.probability(1) == 1.0


def test_the_state_budget_covers_the_truncation_marker(tmp_path):
    (tmp_path / "index.html").write_text("x" * 100, encoding="utf-8")
    (tmp_path / "big.css").write_text("y" * 500, encoding="utf-8")
    (tmp_path / "small.js").write_text("z" * 50, encoding="utf-8")
    included, omitted, truncated, used = evidence.select_state(
        tmp_path, ["big.css", "index.html", "small.js"], budget=400)
    assert [item["path"] for item in included] == ["index.html", "big.css"]
    assert truncated == ["big.css"]
    assert omitted == ["small.js"]
    # The marker is counted inside the budget, so the request cannot run over it.
    assert used == 400
    assert sum(len(item["text"]) for item in included) == used
    assert included[1]["text"].endswith(evidence.TRUNCATION_MARKER.strip())


def test_a_hosted_endpoint_without_a_key_sends_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, "Jev", NeverConstructed)
    project(tmp_path)
    config = settings(tmp_path, jev_base_url="https://api.typesafe.ai/v1",
                      typesafe_api_key="")
    value = task_value()
    result = run(config, value, ["index.html"])
    assert result["status"] == "skipped"
    assert "key" in result["reason"]
    assert NeverConstructed.created == 0
    # The skip is visible in the ledger rather than silent.
    assert value["jev"]["totals"]["calls"] == 1
    assert value["jev"]["totals"]["requests"] == 0


def test_a_disabled_pass_creates_no_client_and_records_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, "Jev", NeverConstructed)
    project(tmp_path)
    config = settings(tmp_path, jev_base_url="https://api.typesafe.ai/v1",
                      typesafe_api_key="k", jev_evidence=False)
    value = task_value()
    assert run(config, value, ["index.html"]) is None
    assert NeverConstructed.created == 0
    assert "jev" not in value


def test_a_local_endpoint_needs_no_key(tmp_path):
    # A self-hosted endpoint is a deliberate configuration and is left alone.
    project(tmp_path)
    config = settings(tmp_path, jev_base_url="http://127.0.0.1:8123/v1")
    client = FakeJev({"r0": {"type": "noul", "noul": 0.9},
                      "r1": {"type": "noul", "noul": 0.9}})
    result = run(config, task_value(), ["index.html"], client=client)
    assert result["status"] == "ran"
    assert len(client.requests) == 1


def test_an_omitted_file_stops_a_low_answer_from_establishing_a_defect(tmp_path):
    project(tmp_path, big="x" * 500)
    config = settings(tmp_path, jev_base_url="http://127.0.0.1:8123/v1")
    client = FakeJev({"r0": {"type": "noul", "noul": 0.02},
                      "r1": {"type": "noul", "noul": 0.02}})
    result = run(config, task_value(), ["index.html", "big"], client=client, budget=400)
    assert result["evidence"]["coverage"] == "incomplete"
    assert result["evidence"]["truncated"] == ["big"]
    assert [finding["verdict"] for finding in result["findings"]] == [
        "insufficient_evidence", "insufficient_evidence"]
    # The raw answer is preserved, so a calibration pass can still see it.
    assert [finding["probability"] for finding in result["findings"]] == [0.02, 0.02]
    # And the model was told what it was not shown.
    limits = client.requests[0][0]["evidence_limits"]
    assert limits["truncated"] == ["big"]
    assert limits["note"]


def test_source_cannot_confirm_a_runtime_requirement(tmp_path):
    project(tmp_path)
    config = settings(tmp_path, jev_base_url="http://127.0.0.1:8123/v1")
    value = task_value(task="- The controls must respond on a tablet touch screen.")
    client = FakeJev({"r0": {"type": "noul", "noul": 0.99}})
    result = run(config, value, ["index.html"], client=client)
    finding = result["findings"][0]
    assert finding["kind"] == "runtime"
    assert finding["probability"] == 0.99
    assert finding["verdict"] == "insufficient_evidence"


def test_an_identical_basis_is_reused_and_a_changed_one_is_not(tmp_path):
    root = project(tmp_path)
    config = settings(tmp_path, jev_base_url="http://127.0.0.1:8123/v1")
    value = task_value(task="- The interface text must be Norwegian.")
    first_client = FakeJev({"r0": {"type": "noul", "noul": 0.9}})
    first = run(config, value, ["index.html"], client=first_client)
    assert first["status"] == "ran" and first["reused"] is False
    value["verification"] = {"jev_evidence": first}
    second_client = FakeJev({"r0": {"type": "noul", "noul": 0.1}})
    second = run(config, value, ["index.html"], client=second_client)
    assert second["reused"] is True
    assert second["assessment_at"] == first["assessment_at"]
    assert second_client.requests == []
    assert value["jev"]["totals"]["reused"] == 1
    # Changed evidence is a different basis and is assessed again.
    (root / "index.html").write_text("<html>endret</html>", encoding="utf-8")
    third_client = FakeJev({"r0": {"type": "noul", "noul": 0.8}})
    third = run(config, value, ["index.html"], client=third_client)
    assert third["reused"] is False
    assert len(third_client.requests) == 1
    assert third["fingerprint"] != first["fingerprint"]


def test_the_ledger_keeps_history_across_repair_rounds(tmp_path):
    root = project(tmp_path)
    config = settings(tmp_path, jev_base_url="http://127.0.0.1:8123/v1",
                      jev_input_price_per_mtok=2.0)
    value = task_value(task="- The interface text must be Norwegian.")
    run(config, value, ["index.html"], client=FakeJev({"r0": {"type": "noul", "noul": 0.9}}))
    (root / "index.html").write_text("<html>reparert</html>", encoding="utf-8")
    run(config, value, ["index.html"], client=FakeJev({"r0": {"type": "noul", "noul": 0.9}}))
    totals = value["jev"]["totals"]
    assert totals["calls"] == 2 and totals["requests"] == 2
    assert totals["input_tokens"] == 20
    assert totals["cost"]["kind"] == "estimated"
    assert totals["cost"]["value"] == pytest.approx(20 / 1_000_000 * 2.0)
    assert [entry["purpose"] for entry in value["jev"]["calls"]] == ["evidence", "evidence"]


def test_an_unpriced_call_is_unknown_rather_than_free(tmp_path):
    project(tmp_path)
    config = settings(tmp_path, jev_base_url="http://127.0.0.1:8123/v1")
    value = task_value(task="- The interface text must be Norwegian.")
    run(config, value, ["index.html"],
        client=FakeJev({"r0": {"type": "noul", "noul": 0.9}}, usage=None))
    totals = value["jev"]["totals"]
    assert totals["unknown_cost_calls"] == 1
    assert totals["cost"]["kind"] == "partial_estimate"
    assert value["jev"]["calls"][0]["cost"]["value"] is None


def test_a_model_failure_is_recorded_rather_than_raised(tmp_path):
    project(tmp_path)
    config = settings(tmp_path, jev_base_url="http://127.0.0.1:8123/v1")
    value = task_value()
    result = run(config, value, ["index.html"],
                 client=FakeJev(error=JevError("JEV returned HTTP 503.")))
    assert result["status"] == "unavailable"
    assert "503" in result["reason"]
    assert result["findings"] == []
    assert value["jev"]["totals"]["requests"] == 0


def test_a_slow_endpoint_is_recorded_as_unavailable_and_the_task_continues(tmp_path):
    project(tmp_path)
    config = settings(tmp_path, jev_base_url="http://127.0.0.1:8123/v1", jev_time_budget=0.3)
    result = run(config, task_value(), ["index.html"], client=FakeJev(delay=5.0))
    assert result["status"] == "unavailable"
    assert "budget" in result["reason"]


def test_cancelling_the_task_is_not_swallowed(tmp_path):
    project(tmp_path)
    config = settings(tmp_path, jev_base_url="http://127.0.0.1:8123/v1", jev_time_budget=30)

    async def cancel_while_running():
        task = asyncio.create_task(evidence.collect(
            config, task_value(), ["index.html"], client=FakeJev(delay=5.0)))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_while_running())


def test_the_pass_respects_the_time_left_in_the_task(tmp_path):
    project(tmp_path)
    config = settings(tmp_path, jev_base_url="http://127.0.0.1:8123/v1", jev_time_budget=30)
    result = run(config, task_value(), ["index.html"], client=FakeJev(), remaining_seconds=0.4)
    assert result["status"] == "skipped"
    assert "second" in result["reason"]


def test_validate_records_the_assessment_beside_the_static_checks(tmp_path, monkeypatch):
    async def fake_collect(settings_object, value, files, **kwargs):
        return {"status": "ran", "model": "jev-test", "evidence": {"coverage": "complete"},
                "findings": [{"requirement": "It must be Norwegian.", "kind": "source",
                              "probability": 0.02, "verdict": "contradicted"}]}

    monkeypatch.setattr("backend.runtime.collect_jev_evidence", fake_collect)
    config = settings(tmp_path)
    store = Store(config.database)
    value = store.create("Write notes.txt that must be Norwegian.", "test/model")
    root = config.workspace_root / value["id"]
    root.mkdir(parents=True, exist_ok=True)
    (root / "notes.txt").write_text("hei", encoding="utf-8")
    runtime = Runtime(config, store, object())
    asyncio.run(runtime.validate({"value": value}))
    assert value["verification"]["jev_evidence"]["status"] == "ran"
    assert any("JEV assessed 1 stated requirements: 1 contradicted" in event["text"]
               for event in value["events"])


def test_a_positive_finding_never_clears_a_failed_deterministic_check(tmp_path, monkeypatch):
    async def fake_collect(settings_object, value, files, **kwargs):
        return {"status": "ran", "model": "jev-test",
                "findings": [{"requirement": "x", "kind": "source", "probability": 0.99,
                              "verdict": "supported"}]}

    monkeypatch.setattr("backend.runtime.collect_jev_evidence", fake_collect)
    config = settings(tmp_path)
    store = Store(config.database)
    value = store.create("Build a thing that must work.", "test/model")
    root = config.workspace_root / value["id"]
    root.mkdir(parents=True, exist_ok=True)
    (root / "broken.py").write_text("def (:\n", encoding="utf-8")
    runtime = Runtime(config, store, object())
    asyncio.run(runtime.validate({"value": value}))
    # The failed parse stands, and the JEV record sits beside it rather than over it.
    assert value["validation_errors"] == ["broken.py"]
    assert value["verification"]["jev_evidence"]["status"] == "ran"


def test_an_unavailable_pass_leaves_the_workflow_alone(tmp_path, monkeypatch):
    async def fake_collect(settings_object, value, files, **kwargs):
        return {"status": "unavailable", "findings": [], "reason": "JEV returned HTTP 503."}

    monkeypatch.setattr("backend.runtime.collect_jev_evidence", fake_collect)
    config = settings(tmp_path)
    store = Store(config.database)
    value = store.create("Write a file that must be there.", "test/model")
    root = config.workspace_root / value["id"]
    root.mkdir(parents=True, exist_ok=True)
    (root / "notes.py").write_text("value = 1\n", encoding="utf-8")
    runtime = Runtime(config, store, object())
    asyncio.run(runtime.validate({"value": value}))
    assert value["verification"]["jev_evidence"]["status"] == "unavailable"
    assert any("JEV evidence unavailable" in event["text"] for event in value["events"])
    assert value["verification"]["checks"]
