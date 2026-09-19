"""Offline tests for the JEV evidence pass. No network, no key, no TypeSafe account."""

import asyncio

from backend import jev_evidence as evidence
from backend.config import Settings
from backend.jev import JevError
from backend.runtime import Runtime
from backend.store import Store


class FakeJev:
    """A JEV client that answers from a script and records what it was asked."""

    def __init__(self, answers=None, error=None):
        self.answers = answers or {}
        self.error = error
        self.requests = []
        self.closed = False

    async def system_one(self, state, questions):
        self.requests.append((state, questions))
        if self.error:
            raise self.error
        return {"answers": self.answers, "usage": {"input_tokens": 10, "output_tokens": 2},
                "resolved_model": "jev-test", "attempts": 1, "latency_ms": 5}

    async def close(self):
        self.closed = True


def settings(tmp_path, **overrides):
    return Settings(api_key="test", workspace_root=tmp_path / "workspaces",
                    database=tmp_path / "tasks.sqlite3",
                    jev_base_url=overrides.pop("jev_base_url", ""), **overrides)


def project(tmp_path, name="index.html", text="<html></html>", **extra):
    root = tmp_path / "workspaces" / "task1"
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(text, encoding="utf-8")
    for other, body in extra.items():
        (root / other).write_text(body, encoding="utf-8")
    return root


def test_requirements_are_taken_verbatim_with_bullets_first():
    task = ("Build a game.\n"
            "- The character must always be visible on screen.\n"
            "- No losing state, and the player can always retry.\n"
            "This paragraph says nothing useful about the product.\n"
            "It must also support touch controls for a tablet.")
    lines = evidence.requirement_lines(task)
    assert lines[0] == "The character must always be visible on screen."
    assert lines[1] == "No losing state, and the player can always retry."
    # The short heading is dropped, and the modal paragraph is kept in order.
    assert "It must also support touch controls for a tablet." in lines
    assert "Build a game." not in lines


def test_requirements_are_capped_and_deduplicated():
    task = "\n".join(f"- Requirement number {index} must hold on every screen." for index in range(14))
    task += "\n- Requirement number 0 must hold on every screen."
    lines = evidence.requirement_lines(task)
    assert len(lines) == evidence.MAX_REQUIREMENTS
    assert len(set(lines)) == len(lines)


def test_only_a_confident_no_is_a_finding():
    assert evidence.verdict(0.02) == "contradicted"
    assert evidence.verdict(0.34) == "contradicted"
    # Near the middle carries no information, so it is not a defect.
    assert evidence.verdict(0.35) == "unclear"
    assert evidence.verdict(0.5) == "unclear"
    assert evidence.verdict(0.8) == "unclear"
    assert evidence.verdict(evidence.SUPPORTED_AT_OR_ABOVE) == "supported"


def test_the_state_is_budgeted_and_records_truncation_and_omission(tmp_path):
    root = tmp_path
    (root / "index.html").write_text("x" * 100, encoding="utf-8")
    (root / "big.css").write_text("y" * 500, encoding="utf-8")
    (root / "small.js").write_text("z" * 50, encoding="utf-8")
    included, omitted, truncated, used = evidence.select_state(
        root, ["big.css", "index.html", "small.js"], budget=400)
    # The entry point goes first, so a one-file game is never crowded out.
    assert [item["path"] for item in included] == ["index.html", "big.css"]
    assert truncated == ["big.css"]
    assert omitted == ["small.js"]
    assert used > 0


def test_findings_keep_the_probability_and_the_usage(tmp_path):
    client = FakeJev({"r0": {"type": "noul", "noul": 0.02},
                      "r1": {"type": "noul", "noul": 0.95}})
    config = settings(tmp_path, jev_base_url="https://example.invalid/v1")
    project(tmp_path)
    result = asyncio.run(evidence.collect(
        config, "task1",
        "- The character must always be visible on screen.\n"
        "- It must support touch controls for a tablet.",
        ["index.html"], client=client))
    assert result["status"] == "ran"
    assert [finding["verdict"] for finding in result["findings"]] == ["contradicted", "supported"]
    assert result["findings"][0]["probability"] == 0.02
    assert result["usage"]["input_tokens"] == 10
    assert result["model"] == "jev-test"
    assert result["evidence"]["included"] == ["index.html"]
    # A caller-owned client is not closed out from under the caller.
    assert client.closed is False
    # One request, one question per requirement, and the state holds the file text.
    state, questions = client.requests[0]
    assert list(questions) == ["r0", "r1"]
    assert state["files"]["index.html"] == "<html></html>"


def test_a_model_failure_is_recorded_rather_than_raised(tmp_path):
    config = settings(tmp_path, jev_base_url="https://example.invalid/v1")
    project(tmp_path)
    result = asyncio.run(evidence.collect(
        config, "task1", "- It must render a visible character on screen.",
        ["index.html"], client=FakeJev(error=JevError("JEV returned HTTP 503."))))
    assert result["status"] == "unavailable"
    assert "503" in result["reason"]
    assert result["findings"] == []


def test_without_a_key_or_with_the_switch_off_nothing_is_recorded(tmp_path):
    project(tmp_path)
    # No endpoint configured: the record stays exactly as it was before JEV existed.
    assert asyncio.run(evidence.collect(settings(tmp_path), "task1",
                                        "- It must render a visible character.", ["index.html"])) is None
    switched_off = settings(tmp_path, jev_base_url="https://example.invalid/v1",
                            jev_evidence=False)
    assert asyncio.run(evidence.collect(switched_off, "task1",
                                        "- It must render a visible character.", ["index.html"])) is None


def test_a_task_without_requirements_is_skipped(tmp_path):
    config = settings(tmp_path, jev_base_url="https://example.invalid/v1")
    project(tmp_path)
    result = asyncio.run(evidence.collect(config, "task1", "Make it nice.", ["index.html"]))
    assert result["status"] == "skipped"
    assert result["findings"] == []


def test_validate_records_jev_evidence_beside_the_static_checks(tmp_path, monkeypatch):
    async def fake_collect(settings, task_id, task, files):
        return {"status": "ran", "model": "jev-test", "findings": [
            {"requirement": "It must render a visible character.",
             "probability": 0.02, "verdict": "contradicted"}]}

    monkeypatch.setattr("backend.runtime.collect_jev_evidence", fake_collect)
    config = settings(tmp_path)
    store = Store(config.database)
    value = store.create("Build a thing that must render a visible character.", "test/model")
    root = config.workspace_root / value["id"]
    root.mkdir(parents=True, exist_ok=True)
    (root / "notes.txt").write_text("hello", encoding="utf-8")
    runtime = Runtime(config, store, object())
    asyncio.run(runtime.validate({"value": value}))
    assert value["verification"]["jev_evidence"]["status"] == "ran"
    assert any("JEV read 1 stated requirements: 1 contradicted" in event["text"]
               for event in value["events"])
