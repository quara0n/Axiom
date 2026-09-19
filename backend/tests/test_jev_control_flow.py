"""JEV reaches the control flow in exactly one place: one bounded repair round."""

import asyncio

from backend.config import Settings
from backend.runtime import Runtime
from backend.store import Store


def build(tmp_path):
    config = Settings(api_key="test", workspace_root=tmp_path / "workspaces",
                      database=tmp_path / "tasks.sqlite3", max_repair_rounds=2)
    store = Store(config.database)
    value = store.create("Write notes.txt that must be Norwegian.", "test/model")
    root = config.workspace_root / value["id"]
    root.mkdir(parents=True, exist_ok=True)
    (root / "notes.txt").write_text("hei", encoding="utf-8")
    runtime = Runtime(config, store, object())
    value["round_history"] = []
    value.setdefault("repair_round", 0)
    value["status"] = "running"
    value["validation_errors"] = []
    value["verification"] = {"mode": "static_only", "execution": {"state": "not_run"},
                             "checks": [], "unsupported_files": []}
    return value, runtime


def state_of(value, review="Looks fine to me."):
    return {"value": value,
            "previous": {"Planner": "plan", "Coder": "wrote notes.txt",
                         "Tester": "checked", "Reviewer": review}}


def contradiction(probability=0.02):
    return {"requirement": "The interface text must be Norwegian.",
            "kind": "source", "probability": probability, "verdict": "contradicted"}


def test_a_confident_contradiction_sends_the_job_back_to_the_coder(tmp_path):
    value, runtime = build(tmp_path)
    value["validation_errors"] = []
    value["review_verdict"] = "approved"
    value["verification"]["jev_evidence"] = {"status": "ran", "findings": [contradiction()]}
    asyncio.run(runtime.finalize(state_of(value)))
    assert value["repair_round"] == 1
    assert value["jev_repair_used"] is True
    assert value["status"] == "running"
    # The requirement travels with the feedback, verbatim, so the Coder knows what to check.
    assert value["repair_feedback"]["jev_findings"][0]["requirement"] == (
        "The interface text must be Norwegian.")
    assert "hypothesis" in value["repair_feedback"]["jev_note"]
    assert any("JEV reports 1 stated requirement" in event["text"] for event in value["events"])
    # The Coder is the one sent back to work.
    assert [agent["status"] for agent in value["agents"] if agent["name"] == "Coder"] == ["pending"]


def test_jev_cannot_approve_and_cannot_clear_a_failure(tmp_path):
    value, runtime = build(tmp_path)
    value["validation_errors"] = ["notes.txt"]
    value["review_verdict"] = "approved"
    # A perfect set of JEV findings changes nothing about a failed parse.
    value["verification"]["jev_evidence"] = {
        "status": "ran",
        "findings": [{"requirement": "x", "kind": "source", "probability": 0.99,
                      "verdict": "supported"}]}
    asyncio.run(runtime.finalize(state_of(value)))
    assert value["repair_round"] == 1        # the failure still sends it back
    assert value["status"] == "running"
    assert value["validation_errors"] == ["notes.txt"]


def test_insufficient_evidence_does_not_change_the_flow(tmp_path):
    value, runtime = build(tmp_path)
    value["validation_errors"] = []
    value["review_verdict"] = "approved"
    value["verification"]["jev_evidence"] = {
        "status": "ran",
        "findings": [{"requirement": "The controls must respond.", "kind": "runtime",
                      "probability": 0.01, "verdict": "insufficient_evidence"},
                     {"requirement": "The build must be reproducible.", "kind": "unknown",
                      "probability": 0.02, "verdict": "insufficient_evidence"}]}
    asyncio.run(runtime.finalize(state_of(value)))
    # Behaviour and unrecognised requirements never trigger work; they are recorded only.
    assert value["repair_round"] == 0
    assert value["status"] == "completed"


def test_only_one_jev_triggered_repair_per_task(tmp_path):
    value, runtime = build(tmp_path)
    value["validation_errors"] = []
    value["review_verdict"] = "approved"
    value["verification"]["jev_evidence"] = {"status": "ran", "findings": [contradiction()]}
    asyncio.run(runtime.finalize(state_of(value)))
    assert value["repair_round"] == 1
    # The same contradiction on the next pass does not buy a second round: the project did
    # not change, so the loop stops rather than repeating itself.
    value["review_verdict"] = "approved"
    asyncio.run(runtime.finalize(state_of(value, review="Still fine.")))
    assert value["repair_round"] == 1
    # JEV got its one round. The Reviewer then approved on its own authority, so the task
    # completes - but the contradiction stays visible rather than disappearing.
    assert value["status"] == "completed"
    assert value["jev_unresolved"] == ["The interface text must be Norwegian."]
    assert any("still contradicted" in event["text"] for event in value["events"])
