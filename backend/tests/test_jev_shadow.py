"""Offline tests for the shadow recommendation. No network, no key, no account.

Shadow mode is the only JEV use that recommends an action, so these tests care most
about what it must *not* do: change a permission, a tool menu, a role or a verdict.
"""

import asyncio

from backend import jev_shadow as shadow
from backend.config import Settings
from backend.control import RepeatGuard


class FakeChoice:
    """Answers a choice question from a script."""

    def __init__(self, answer=None, error=None):
        self.answer = answer or {}
        self.error = error
        self.requests = []
        self.closed = False

    async def system_one(self, state, questions):
        self.requests.append((state, questions))
        if self.error:
            raise self.error
        return {"answers": {"next": self.answer}, "usage": {"input_tokens": 7, "output_tokens": 3},
                "resolved_model": "jev-test", "attempts": 1, "latency_ms": 4}

    async def close(self):
        self.closed = True


def settings(tmp_path, **overrides):
    return Settings(api_key="test", workspace_root=tmp_path / "workspaces",
                    database=tmp_path / "tasks.sqlite3",
                    jev_base_url=overrides.pop("jev_base_url", "http://127.0.0.1:8123/v1"),
                    jev_shadow=overrides.pop("jev_shadow", True), **overrides)


def task_value(**overrides):
    value = {"id": "task1", "task": "- The controls must respond to the keyboard.",
             "tool_trace": {"calls": [
                 {"tool": "read_file", "outcome": "completed", "result_bytes": 900},
                 {"tool": "read_file", "outcome": "completed", "result_bytes": 900}]},
             "verification": {"checks": [{"path": "index.html", "valid": True}]}}
    value.update(overrides)
    return value


def test_the_options_fit_the_role_and_what_the_harness_allows():
    tester = shadow.actions_for("Tester", allows_execution=False, is_coder=False)
    # A Tester cannot hand itself shell access, so the runtime-check option is absent.
    assert shadow.RUN_CHECK not in tester
    assert "Report the problem" in tester[shadow.REPAIR]
    coder = shadow.actions_for("Coder", allows_execution=True, is_coder=True)
    assert shadow.RUN_CHECK in coder
    assert "Fix the problem" in coder[shadow.REPAIR]
    assert "grants no access" in coder[shadow.RUN_CHECK]


def test_the_actual_next_action_uses_the_same_vocabulary():
    assert shadow.classify_next(["read_file"], finished=False) == shadow.CONTINUE
    assert shadow.classify_next(["write_file"], finished=False) == shadow.REPAIR
    assert shadow.classify_next([], finished=True) == shadow.FINISH
    assert shadow.classify_next(["blender__make_cube"], finished=False) == shadow.RUN_CHECK
    # A name the harness never accepted is not an action, let alone a runtime check.
    assert shadow.classify_next(["made_up_tool"], finished=False) == shadow.INSUFFICIENT
    assert shadow.classify_next([], finished=False) == shadow.INSUFFICIENT


def test_only_unchanged_repeated_reads_raise_the_stagnation_signal():
    guard = RepeatGuard()
    same = {"text": "unchanged"}
    signals = [guard.observe("read_file", {"path": "a"}, same, read=True) for _ in range(3)]
    assert signals == [False, False, False]
    # The fourth identical observation is the signal the shadow assessment hangs on.
    assert guard.observe("read_file", {"path": "a"}, same, read=True) is True
    # Reading something that actually changed is progress, not stagnation.
    assert guard.observe("read_file", {"path": "a"}, {"text": "changed"}, read=True) is False


def test_a_recommendation_is_recorded_next_to_what_actually_happened(tmp_path):
    config = settings(tmp_path)
    value = task_value()
    client = FakeChoice({"type": "choice", "choice": shadow.CONTINUE,
                         "probabilities": {shadow.CONTINUE: 0.71}, "confidence": 0.71})
    entry = asyncio.run(shadow.assess(config, value, "Tester", repeated=True, rounds=5,
                                      allows_execution=False, client=client))
    assert entry["status"] == "ran"
    assert entry["recommendation"] == shadow.CONTINUE
    assert entry["probability"] == 0.71
    assert entry["followed"] is None
    # The basis is compact and carries no file contents.
    assert entry["basis"]["repeated_call_without_progress"] is True
    assert entry["basis"]["recent_calls"][0]["tool"] == "read_file"
    assert "content" not in entry["basis"]
    # What the workflow actually did is attached afterwards, and kept distinct.
    shadow.note_next_action(value, shadow.REPAIR)
    assert entry["actual_next_action"] == shadow.REPAIR
    assert entry["followed"] is False
    assert value["jev"]["totals"]["calls"] == 1
    assert value["jev"]["calls"][0]["purpose"] == "shadow"


def test_the_assessment_count_is_capped_per_task(tmp_path):
    config = settings(tmp_path, jev_shadow_max=1)
    value = task_value()
    answer = {"type": "choice", "choice": shadow.FINISH, "probabilities": {shadow.FINISH: 0.9}}
    first = asyncio.run(shadow.assess(config, value, "Tester", repeated=True, rounds=5,
                                      client=FakeChoice(answer)))
    second = asyncio.run(shadow.assess(config, value, "Tester", repeated=True, rounds=6,
                                       client=FakeChoice(answer)))
    assert first is not None
    assert second is None
    assert len(value["jev_shadow"]["assessments"]) == 1


def test_shadow_mode_off_or_without_a_key_sends_nothing(tmp_path, monkeypatch):
    value = task_value()
    off = settings(tmp_path, jev_shadow=False)
    assert asyncio.run(shadow.assess(off, value, "Tester", repeated=True,
                                     client=FakeChoice({}))) is None
    hosted = settings(tmp_path, jev_base_url="https://api.typesafe.ai/v1",
                      typesafe_api_key="")
    assert asyncio.run(shadow.assess(hosted, value, "Tester", repeated=True,
                                     client=FakeChoice({}))) is None
    assert "jev_shadow" not in value


def test_a_failed_recommendation_is_recorded_and_changes_nothing(tmp_path):
    config = settings(tmp_path)
    value = task_value()
    before = set(value)
    entry = asyncio.run(shadow.assess(config, value, "Tester", repeated=True, rounds=4,
                                      client=FakeChoice(error=RuntimeError("boom"))))
    assert entry["status"] == "unavailable"
    assert entry["recommendation"] is None
    # The only keys added are the shadow record and its ledger: no routing, no role
    # change, no permission and no verdict is touched.
    assert set(value) == before | {"jev_shadow", "jev"}


def test_the_shadow_record_cannot_touch_a_verdict_or_the_tool_menu(tmp_path):
    config = settings(tmp_path)
    value = task_value(review_verdict="changes_required", files=["index.html"])
    snapshot = {"review_verdict": value["review_verdict"], "files": list(value["files"])}
    asyncio.run(shadow.assess(config, value, "Reviewer", repeated=True, rounds=3,
                              client=FakeChoice({"type": "choice", "choice": shadow.REPAIR,
                                                 "probabilities": {shadow.REPAIR: 0.8}})))
    assert value["review_verdict"] == snapshot["review_verdict"]
    assert value["files"] == snapshot["files"]
    # A Reviewer may recommend a repair, but the recommendation stays a record.
    assert value["jev_shadow"]["assessments"][0]["recommendation"] == shadow.REPAIR
