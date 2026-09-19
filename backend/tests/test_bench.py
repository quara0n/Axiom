"""The benchmark's job is to be able to say no. These tests check that it can."""

import json
import shutil
from pathlib import Path

import pytest

from backend.bench import (DEFAULT_SUITE, find_tasks, load_task, parse_score,
                           render_report, resource_record, run_cell, run_suite, summarize,
                           validate_task, wilson_interval)
from backend.config import Settings


def settings(tmp_path, **overrides):
    return Settings(api_key=overrides.pop("api_key", "test-backend-secret"),
                    workspace_root=tmp_path / "workspaces",
                    database=tmp_path / "tasks.sqlite3", **overrides)


def make_task(root: Path, checker_body: str, *, solution: bool = True):
    (root / "workspace").mkdir(parents=True)
    (root / "workspace" / "file.txt").write_text("start\n", encoding="utf-8")
    (root / "checker").mkdir()
    (root / "checker" / "checker.py").write_text(checker_body, encoding="utf-8")
    (root / "task.json").write_text(json.dumps({"name": root.name, "prompt": "Do it."}),
                                    encoding="utf-8")
    if solution:
        (root / "solution").mkdir()
        (root / "solution" / "file.txt").write_text("done\n", encoding="utf-8")
    return load_task(root)


ALWAYS_PASSES = "print('SCORE: 1/1')\n"
ALWAYS_FAILS = "print('SCORE: 0/1')\nraise SystemExit(1)\n"
CHECKS_CONTENT = (
    "import sys\n"
    "from pathlib import Path\n"
    "text = (Path(sys.argv[1]) / 'file.txt').read_text()\n"
    "ok = text.strip() == 'done'\n"
    "print(f'SCORE: {1 if ok else 0}/1')\n"
    "raise SystemExit(0 if ok else 1)\n")


def test_wilson_interval_brackets_the_estimate():
    assert wilson_interval(3, 3) == (0.4385, 1.0)
    assert wilson_interval(0, 3) == (0.0, 0.5615)
    assert wilson_interval(0, 0) == (0.0, 1.0)
    low, high = wilson_interval(50, 100)
    assert low < 0.5 < high
    assert high - low < 0.2


def usage_cell(solved, cost, prompts=100, unknown=0):
    return {"arm": "roles", "solved": solved, "cost_usd": cost,
            "tokens": {"prompt_tokens": prompts}, "unknown_usage_calls": unknown,
            "wall_ms": 10, "model_calls": 1}


def test_cost_per_solve_includes_failed_attempts():
    result = summarize([usage_cell(True, 1), usage_cell(False, 9, 900)])["roles"]
    assert result["solve_rate"] == 0.5
    assert result["cost_per_solve_usd"] == 10
    assert result["prompt_tokens_per_solve"] == 1000


@pytest.mark.parametrize("failure", [usage_cell(False, None, None),
                                     usage_cell(False, 1, unknown=1)])
def test_incomplete_usage_is_not_a_cheap_solve(failure):
    result = summarize([usage_cell(True, 1), failure])["roles"]
    assert result["cost_per_solve_usd"] is None
    assert result["prompt_tokens_per_solve"] is None


def test_no_solutions_has_no_per_solve_cost():
    result = summarize([usage_cell(False, 3)])["roles"]
    assert result["cost_per_solve_usd"] is None
    assert result["prompt_tokens_per_solve"] is None


def test_a_checker_that_accepts_anything_is_caught(tmp_path):
    task = make_task(tmp_path / "task", ALWAYS_PASSES)
    entry = validate_task(task, tmp_path / "validation")
    assert entry["valid"] is False
    assert "accepts the untouched workspace" in entry["problems"][0]


def test_a_checker_that_rejects_its_own_solution_is_caught(tmp_path):
    task = make_task(tmp_path / "task", ALWAYS_FAILS)
    entry = validate_task(task, tmp_path / "validation")
    assert entry["valid"] is False
    assert entry["problems"] == ["the checker rejects its own golden solution"]


def test_a_task_without_a_solution_cannot_be_trusted(tmp_path):
    task = make_task(tmp_path / "task", CHECKS_CONTENT, solution=False)
    entry = validate_task(task, tmp_path / "validation")
    assert entry["valid"] is False
    assert "unproven on a correct answer" in entry["problems"][0]


def test_a_discriminating_checker_validates(tmp_path):
    task = make_task(tmp_path / "task", CHECKS_CONTENT)
    entry = validate_task(task, tmp_path / "validation")
    assert entry["valid"] is True, entry["problems"]
    assert entry["runs"]["null"]["exit_code"] != 0
    assert entry["runs"]["golden"]["exit_code"] == 0


def test_partial_credit_is_read_without_rounding_up():
    assert parse_score("pass\nSCORE: 7/9\n") == pytest.approx(0.7778, abs=1e-4)
    assert parse_score("SCORE: 0.5") == 0.5
    assert parse_score("no score here") is None


def test_find_tasks_reads_the_shipped_suite():
    names = {task.name for task in find_tasks(DEFAULT_SUITE)}
    assert names == {"fix-failing-test", "build-a-cli", "three-d-game", "angry-birds"}


def test_every_shipped_task_discriminates(tmp_path):
    """The suite is only worth running if each checker rejects the starting files
    and accepts the golden solution."""
    if shutil.which("node") is None:
        pytest.skip("node is not on PATH")
    for task in find_tasks(DEFAULT_SUITE):
        entry = validate_task(task, tmp_path / task.name)
        assert entry["valid"] is True, (task.name, entry["problems"])


def test_a_cell_is_graded_by_the_checker_not_by_the_harness(tmp_path):
    if shutil.which("node") is None:
        pytest.skip("node is not on PATH")
    task = load_task(DEFAULT_SUITE / "fix-failing-test")
    record = run_cell(task, "single", settings(tmp_path), "golden",
                      tmp_path / "run", repeat=1)
    assert record["solved"] is True
    assert record["checker"]["exit_code"] == 0
    # The single arm reports no verdict of its own, so nothing here can agree with
    # the checker by accident.
    assert record["harness_verdict"] is None


def test_the_negative_control_solves_nothing(tmp_path):
    if shutil.which("node") is None:
        pytest.skip("node is not on PATH")
    task = load_task(DEFAULT_SUITE / "fix-failing-test")
    record = run_cell(task, "null", settings(tmp_path), "void", tmp_path / "run", repeat=1)
    assert record["solved"] is False
    assert record["model_calls"] == 0
    assert record["cost_usd"] is None


def test_a_run_without_a_cost_cap_is_refused_when_it_could_spend(tmp_path):
    with pytest.raises(ValueError, match="max-cost-usd"):
        run_suite(DEFAULT_SUITE, ("roles",), ["fix-failing-test"], 1, "openrouter",
                  settings(tmp_path), tmp_path / "results")


def test_the_cost_cap_stops_the_run(tmp_path):
    if shutil.which("node") is None:
        pytest.skip("node is not on PATH")
    run = run_suite(DEFAULT_SUITE, ("single", "null"), ["fix-failing-test", "build-a-cli"],
                    1, "golden", settings(tmp_path), tmp_path / "results", max_cost_usd=0.0)
    assert run["stopped_at_cost_cap"] is True
    assert len(run["records"]) == 1


def test_the_report_separates_the_harness_verdict_from_the_checker(tmp_path):
    """The report must not let a self-report pass for a measurement."""
    if shutil.which("node") is None:
        pytest.skip("node is not on PATH")
    run = run_suite(DEFAULT_SUITE, ("null",), ["fix-failing-test"], 1, "void",
                    settings(tmp_path), tmp_path / "results")
    report = render_report(run)
    assert "Did the harness know?" in report
    assert "cross-benchmark" in report
    assert "Visual and design quality" in report
    assert summarize(run["records"])["null"]["solved"] == 0

def test_jev_spend_belongs_to_the_cell_total():
    """A benchmark that reads only the model cost reports a run as cheaper than it was."""
    value = {"usage": {"totals": {"cost": 0.10, "prompt_tokens": 100, "unknown_calls": 0},
                       "by_role": {}},
             "jev": {"totals": {"calls": 2, "requests": 2, "reused": 0, "input_tokens": 500,
                                "unknown_cost_calls": 0,
                                "cost": {"value": 0.0005, "kind": "estimated"}}}}
    record = resource_record(value)
    assert record["cost_usd"] == 0.10
    assert record["jev"]["cost_usd"] == 0.0005
    assert record["jev"]["calls"] == 2
    assert record["total_cost_usd"] == pytest.approx(0.1005)
    # A JEV call whose price is unknown makes the total unknown, never cheaper.
    value["jev"]["totals"]["unknown_cost_calls"] = 1
    assert resource_record(value)["total_cost_usd"] is None


def test_the_summary_and_report_show_jev():
    records = [{"arm": "roles", "task": "sample", "solved": True, "wall_ms": 1000,
                "cost_usd": 0.10, "unknown_usage_calls": 0, "total_cost_usd": 0.1005,
                "tokens": {"prompt_tokens": 100}, "model_calls": 3,
                "jev": {"calls": 2, "requests": 2, "reused": 0, "input_tokens": 500,
                        "unknown_cost_calls": 0, "cost_usd": 0.0005}}]
    summary = summarize(records)
    assert summary["roles"]["jev_calls"] == 2
    assert summary["roles"]["jev_cost_usd"] == pytest.approx(0.0005)
    assert summary["roles"]["cost_per_solve_usd"] == pytest.approx(0.1005)
    report = render_report({"started_at": "2026-09-20T00:00:00Z", "provider": "golden",
                            "arms": ["roles"], "tasks": [], "repeats": 1,
                            "max_cost_usd": None, "harness": "test", "records": records,
                            "validation": [], "summary": summary})
    assert "JEV calls" in report and "JEV cost" in report
