"""A benchmark the harness can lose.

Axiom has never had a number it could be judged by. The task store records what the
team reported about itself, and the Reviewer that approves a run is part of the same
run, so "approved" is a claim rather than a measurement. This module supplies the
missing half: tasks with a checker the harness cannot edit, a negative control that
must fail, a golden solution that must pass, and one headline metric - cost and
wall-clock per *solved* task.

The shape follows the published harness benchmarks this project measured itself
against (HarnessTax, OpenBench): checkers validated against both a null and a golden
run, Wilson intervals instead of point estimates, and an explicit cost cap before
anything is spent.

What it does not measure: visual or design quality. A checker grades behaviour it
can execute. Design is recorded as an operator rubric in the report and is never
folded into the solve rate.
"""

import argparse
import asyncio
import dataclasses
import json
import math
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .provider import OpenRouter, ProviderError
from .runner import execution_environment
from .single_loop import SingleLoop
from .store import ROLES, Store

ARMS = ("roles", "single", "null")

# A checker prints this to award partial credit. Exit code 0 is still the only thing
# that counts as solved, so a partially correct run is visible without being rounded
# up into a success.
SCORE_PATTERN = re.compile(r"SCORE:\s*(\d+(?:\.\d+)?)\s*(?:/\s*(\d+(?:\.\d+)?))?")

DEFAULT_SUITE = Path(__file__).resolve().parent.parent / "bench" / "tasks"


@dataclass
class BenchTask:
    name: str
    prompt: str
    root: Path
    workspace: Path
    checker: Path
    solution: Path | None
    timeout: float
    notes: str = ""


# --------------------------------------------------------------------------- tasks

def load_task(root: Path) -> BenchTask:
    # Absolute paths, because the checker runs with the task root as its working
    # directory and a relative checker path would resolve against itself.
    root = Path(root).resolve()
    manifest_path = root / "task.json"
    if not manifest_path.is_file():
        raise ValueError(f"{root.name}: task.json is required.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"{root.name}: task.json is not valid JSON.") from exc
    prompt = manifest.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError(f"{root.name}: task.json needs a non-empty prompt.")
    checker = root / "checker" / "checker.py"
    if not checker.is_file():
        raise ValueError(f"{root.name}: checker/checker.py is required.")
    workspace = root / "workspace"
    if not workspace.is_dir():
        raise ValueError(f"{root.name}: a workspace/ directory is required.")
    solution = root / "solution"
    return BenchTask(
        name=manifest.get("name") or root.name, prompt=prompt, root=root,
        workspace=workspace, checker=checker,
        solution=solution if solution.is_dir() else None,
        timeout=float(manifest.get("timeout", 300)),
        notes=str(manifest.get("notes", "")),
    )


def find_tasks(suite: Path) -> list[BenchTask]:
    suite = Path(suite)
    if not suite.is_dir():
        raise ValueError(f"No task suite at {suite}.")
    tasks = [load_task(child) for child in sorted(suite.iterdir())
             if child.is_dir() and (child / "task.json").is_file()]
    if not tasks:
        raise ValueError(f"No tasks with a task.json under {suite}.")
    return tasks


def seed_workspace(task: BenchTask, target: Path):
    """Copy the task's starting files. The checker and the solution never travel
    into the workspace: a harness that can read its own grader is not being graded.
    """
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(task.workspace, target)


def solution_files(task: BenchTask) -> dict[str, str]:
    if task.solution is None:
        return {}
    files = {}
    for path in sorted(task.solution.rglob("*")):
        if path.is_file():
            relative = path.relative_to(task.solution).as_posix()
            files[relative] = path.read_text(encoding="utf-8")
    return files


# ------------------------------------------------------------------------ statistics

def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval. With a handful of cells a point estimate is noise, and
    two arms are only different when their intervals separate."""
    if total <= 0:
        return (0.0, 1.0)
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    spread = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return (round(max(0.0, centre - spread), 4), round(min(1.0, centre + spread), 4))


def _median(values):
    values = [value for value in values if isinstance(value, (int, float))]
    return round(statistics.median(values), 3) if values else None


def parse_score(output: str):
    match = SCORE_PATTERN.search(output or "")
    if not match:
        return None
    value = float(match.group(1))
    if match.group(2) is not None:
        total = float(match.group(2))
        return round(value / total, 4) if total else None
    return round(value, 4)


# --------------------------------------------------------------------------- grader

def run_checker(task: BenchTask, workspace: Path, timeout: float | None = None) -> dict:
    """Run the task's own checker against a workspace and read only its exit code."""
    # The checker runs with the task root as its working directory, so both paths it
    # receives have to be absolute or a relative one resolves against the task.
    workspace = Path(workspace).resolve()
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            [sys.executable, str(task.checker), str(workspace)],
            cwd=str(task.root), env=execution_environment(),
            capture_output=True, text=True, timeout=timeout or task.timeout, check=False)
        exit_code, timed_out = completed.returncode, False
        output = (completed.stdout or "") + (completed.stderr or "")
    except subprocess.TimeoutExpired as exc:
        exit_code, timed_out = None, True
        output = _text(exc.stdout) + _text(exc.stderr)
    except (OSError, subprocess.SubprocessError) as exc:
        exit_code, timed_out = None, False
        output = f"{type(exc).__name__}: the checker could not be started."
    return {
        "exit_code": exit_code, "timed_out": timed_out,
        "duration_ms": round((time.perf_counter() - started) * 1000),
        "score": parse_score(output),
        "crashed": "Traceback (most recent call last)" in output,
        "output": output.strip()[-4000:],
    }


def _text(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value or ""


def validate_task(task: BenchTask, root: Path) -> dict:
    """Prove the checker discriminates before trusting anything it says.

    An untouched workspace must fail, and the golden solution must pass. A checker
    that accepts the starting files scores every harness as perfect; one that rejects
    the solution makes every harness look broken. Both are silent failures without
    this step.
    """
    problems, results = [], {}
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)

    null_workspace = root / "null" / "workspace"
    seed_workspace(task, null_workspace)
    null = run_checker(task, null_workspace)
    results["null"] = null
    if null["exit_code"] == 0:
        problems.append("the checker accepts the untouched workspace")
    elif null["crashed"]:
        results["null_warning"] = ("the null run failed with a traceback, so its "
                                   "failure is an error rather than a verdict")

    if task.solution is None:
        results["golden"] = None
        problems.append("no solution/ directory, so the checker is unproven on a "
                        "correct answer")
    else:
        golden_workspace = root / "golden" / "workspace"
        seed_workspace(task, golden_workspace)
        for relative, content in solution_files(task).items():
            target = golden_workspace / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        golden = run_checker(task, golden_workspace)
        results["golden"] = golden
        if golden["exit_code"] != 0:
            problems.append("the checker rejects its own golden solution")
    return {"task": task.name, "valid": not problems, "problems": problems,
            "runs": results}


# ------------------------------------------------------------------------- providers

class VoidProvider:
    """Spends nothing and writes nothing. The negative control for a whole arm."""

    async def close(self):
        pass

    async def models(self):
        return []

    async def complete(self, model, messages, tools):
        raise ProviderError("The void provider never answers.")


class GoldenProvider:
    """Writes the task's golden solution through the harness's own tools.

    This is the end-to-end positive control: it proves that the pipeline can reach
    the grader and that a correct answer scores, without paying a model to prove it.
    """

    def __init__(self, files: dict[str, str]):
        self.files = list(files.items())
        self.written = 0

    async def close(self):
        pass

    async def models(self):
        return []

    async def complete(self, model, messages, tools):
        system = messages[0]["content"] if messages else ""
        if "You are Planner." in system:
            if len(messages) == 2:
                return _call("list_files", {})
            return _text_message("Plan: write the files the task asks for.")
        if "working alone" in system or "You are Coder." in system:
            if self.written < len(self.files):
                name, content = self.files[self.written]
                self.written += 1
                return _call("write_file", {"path": name, "content": content})
            return _text_message("Wrote every file the task asked for.")
        if "You are Tester." in system:
            if len(messages) == 2:
                return _call("read_file", {"path": "AGENTS.md"})
            return _text_message("Static checks only; nothing was executed here.")
        if "You are Reviewer." in system:
            if len(messages) == 2:
                return _call("read_file", {"path": "AGENTS.md"})
            return _text_message(json.dumps({"verdict": "approved",
                                             "summary": "Approved on inspection."}))
        return _text_message("Nothing to do.")


def _call(name, args):
    return {"content": None, "tool_calls": [{
        "id": f"{name}-{uuid.uuid4().hex[:8]}", "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)}}]}


def _text_message(text):
    return {"content": text}


def provider_for(kind: str, task: BenchTask, settings: Settings):
    if kind == "void":
        return VoidProvider()
    if kind == "golden":
        files = solution_files(task)
        if not files:
            raise ValueError(f"{task.name} has no solution/ for the golden provider.")
        return GoldenProvider(files)
    if kind == "openrouter":
        return OpenRouter(settings.api_key, max_tokens=settings.max_tokens,
                          reasoning_max_tokens=settings.reasoning_max_tokens,
                          request_timeout=settings.request_timeout,
                          max_attempts=settings.max_provider_attempts,
                          retry_base=settings.provider_retry_base)
    raise ValueError(f"Unknown provider {kind!r}.")


# ------------------------------------------------------------------------------ cells

def run_cell(task: BenchTask, arm: str, config: Settings, kind: str,
             run_dir: Path, repeat: int, models: dict[str, str] | None = None) -> dict:
    """Run one harness against one task and grade the result.

    The harness never sees the checker, and the grader reads only the exit code, so
    a run cannot pass by asserting that it passed.
    """
    cell = f"{task.name}--{arm}--r{repeat}"
    cell_root = run_dir / "cells" / cell
    cell_settings = dataclasses.replace(
        config, workspace_root=cell_root / "work", database=run_dir / "bench.sqlite3",
        task_timeout=task.timeout)
    store = Store(cell_settings.database)
    value = store.create(task.prompt, cell_settings.model, models)
    workspace_path = cell_settings.workspace_root / value["id"]
    seed_workspace(task, workspace_path)
    record = {"cell": cell, "task": task.name, "arm": arm, "repeat": repeat,
              "task_id": value["id"], "model": cell_settings.model,
              "models": value.get("models") or {}, "provider": kind}
    provider = None
    started = time.perf_counter()
    if arm == "null":
        # The negative control does not call a model at all: it is the harness doing
        # nothing, which is what a checker must be able to tell apart from solving.
        value["status"] = "completed"
        value["model_calls"] = 0
    else:
        provider = provider_for(kind, task, cell_settings)
        try:
            asyncio.run(_run_arm(arm, value, cell_settings, provider))
        except Exception as exc:  # the cell is a data point, not a crash
            value["status"] = "failed"
            value["error"] = f"{type(exc).__name__}: {exc}"[:400]
        finally:
            if provider is not None:
                try:
                    asyncio.run(provider.close())
                except Exception:
                    pass
    record["wall_ms"] = round((time.perf_counter() - started) * 1000)
    record["status"] = value.get("status")
    record["error"] = value.get("error")
    record["model_calls"] = value.get("model_calls")
    record["repair_rounds"] = value.get("repair_round")
    record["harness_verdict"] = value.get("review_verdict")
    record["files"] = value.get("files") or []
    usage = value.get("usage") or {}
    totals = usage.get("totals") or {}
    record["tokens"] = {name: totals.get(name) for name in
                        ("prompt_tokens", "completion_tokens", "reasoning_tokens",
                         "cached_tokens")}
    record["cost_usd"] = totals.get("cost")
    record["unknown_usage_calls"] = totals.get("unknown_calls", 0)
    record["by_role"] = usage.get("by_role") or {}
    verification = value.get("verification") or {}
    record["verification"] = {"mode": verification.get("mode"),
                              "runtime_tested": verification.get("runtime_tested")}
    record["checker"] = run_checker(task, workspace_path)
    record["solved"] = record["checker"]["exit_code"] == 0
    record["score"] = record["checker"]["score"]
    store.save(value)
    (cell_root / "record.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


async def _run_arm(arm, value, settings, provider):
    from .runtime import Runtime

    runtime = Runtime(settings, Store(settings.database), provider)
    try:
        if arm == "roles":
            await runtime.run(value)
        elif arm == "single":
            try:
                value["summary"] = await SingleLoop(runtime, value).run()
                value["status"] = "completed"
            except ProviderError as exc:
                value["status"] = "failed"
                value["error"] = str(exc)
        else:
            raise ValueError(f"Unknown arm {arm!r}.")
    finally:
        await runtime.close()


# ---------------------------------------------------------------------------- report

def summarize(records: list[dict]) -> dict:
    summary = {}
    for arm in ARMS:
        cells = [record for record in records if record["arm"] == arm]
        if not cells:
            continue
        solved = [record for record in cells if record["solved"]]
        # All attempts consume resources, including failures. Partial usage must
        # not appear as a complete (artificially cheap) cost per solved task.
        costs = [record["cost_usd"] for record in cells
                 if isinstance(record["cost_usd"], (int, float))]
        prompts = [record["tokens"]["prompt_tokens"] for record in cells
                   if isinstance(record["tokens"]["prompt_tokens"], (int, float))]
        usage_complete = not any(record.get("unknown_usage_calls") for record in cells)
        summary[arm] = {
            "cells": len(cells),
            "solved": len(solved),
            "solve_rate": round(len(solved) / len(cells), 4),
            "wilson95": wilson_interval(len(solved), len(cells)),
            "median_wall_ms": _median([record["wall_ms"] for record in cells]),
            "median_cost_usd": _median([record["cost_usd"] for record in cells]),
            "cost_per_solve_usd": (round(sum(costs) / len(solved), 4)
                                   if solved and usage_complete and len(costs) == len(cells) else None),
            "prompt_tokens_per_solve": (round(sum(prompts) / len(solved))
                                        if solved and usage_complete and len(prompts) == len(cells) else None),
            "cells_without_usage": sum(1 for record in cells
                                       if record.get("unknown_usage_calls")),
            "median_model_calls": _median([record["model_calls"] for record in cells]),
        }
    return summary


def render_report(run: dict) -> str:
    records, summary = run["records"], run["summary"]
    lines = ["# Benchmark run", "",
             f"- Started: {run['started_at']}",
             f"- Provider: `{run['provider']}`",
             f"- Arms: {', '.join(run['arms'])}",
             f"- Tasks: {', '.join(run['tasks'])}",
             f"- Repeats per cell: {run['repeats']}",
             f"- Cost cap: "
             + (f"${run['max_cost_usd']:.2f}" if run.get("max_cost_usd") is not None
                else "none (provider cannot spend)"),
             f"- Harness: {run['harness']}", ""]

    lines += ["## Checker validation", "",
              "| Task | Untouched workspace | Golden solution | Valid |", "|---|---|---|---|"]
    for entry in run["validation"]:
        runs = entry["runs"]
        null = "fails" if runs["null"]["exit_code"] != 0 else "PASSES (broken)"
        golden = runs.get("golden")
        golden_text = ("passes" if golden and golden["exit_code"] == 0
                       else "FAILS (broken)" if golden else "no solution")
        lines.append(f"| {entry['task']} | {null} | {golden_text} | "
                     f"{'yes' if entry['valid'] else 'no'} |")
    lines.append("")
    for entry in run["validation"]:
        for problem in entry["problems"]:
            lines.append(f"- **{entry['task']}**: {problem}")
    lines.append("")

    lines += ["## Result per arm", "",
              "| Arm | Solved | Solve rate | Wilson 95% | Median wall | Median cost | "
              "Cost per solve | Prompt tokens per solve | Model calls |", "|---|---|---|---|---|---|---|---|---|"]
    for arm, row in summary.items():
        cost = f"${row['cost_per_solve_usd']:.3f}" if row["cost_per_solve_usd"] is not None else "n/a"
        median_cost = (f"${row['median_cost_usd']:.3f}"
                       if row["median_cost_usd"] is not None else "n/a")
        tokens = row["prompt_tokens_per_solve"] if row["prompt_tokens_per_solve"] is not None else "n/a"
        lines.append(
            f"| {arm} | {row['solved']}/{row['cells']} | {row['solve_rate']:.1%} | "
            f"[{row['wilson95'][0]:.3f}, {row['wilson95'][1]:.3f}] | "
            f"{_seconds(row['median_wall_ms'])} | {median_cost} | {cost} | {tokens} | "
            f"{row['median_model_calls']} |")
    lines.append("")

    lines += ["Per-solve resources include every attempted cell, including failures. "
              "They are n/a when usage is incomplete or no task was solved.", "",
              "## Cell matrix", "",
              "| Task | " + " | ".join(run["arms"]) + " |", "|---|" + "---|" * len(run["arms"])]
    for task in run["tasks"]:
        cells = []
        for arm in run["arms"]:
            matching = [record for record in records
                        if record["task"] == task and record["arm"] == arm]
            if not matching:
                cells.append("-")
                continue
            solved = sum(1 for record in matching if record["solved"])
            scores = [record["score"] for record in matching
                      if isinstance(record["score"], (int, float))]
            partial = f" ({statistics.mean(scores):.0%} partial)" if scores and solved < len(matching) else ""
            cells.append(f"{solved}/{len(matching)}{partial}")
        lines.append(f"| {task} | " + " | ".join(cells) + " |")
    lines.append("")

    lines += ["## Did the harness know?", "",
              "`approved` is Axiom's own verdict; `solved` is the external checker. "
              "Where they disagree, the run believed something that was not true.", "",
              "| Task | Arm | Harness verdict | Checker | Agreement |", "|---|---|---|---|---|"]
    for record in records:
        verdict = record.get("harness_verdict") or "-"
        solved = "solved" if record["solved"] else ("failed" if record["checker"]["exit_code"] is not None
                                                    else "no verdict")
        if record.get("harness_verdict") is None:
            agreement = "-"
        else:
            agreement = "yes" if (record["harness_verdict"] == "approved") == record["solved"] else "**no**"
        lines.append(f"| {record['task']} | {record['arm']} | {verdict} | {solved} | {agreement} |")
    lines.append("")

    lines += ["## What this does not measure", "",
              "- Visual and design quality. The checkers grade behaviour they can "
              "execute; `design.md` beside this report is the operator's rubric and "
              "is deliberately kept out of the solve rate.",
              "- Any comparison with a published harness number. Different tasks, "
              "different graders and different models make cross-benchmark numbers "
              "meaningless; only cells from this same run are comparable.",
              "- The `single` arm has no internal gate: it reports no verdict, so the "
              "agreement table has nothing to compare for it.", ""]
    return "\n".join(lines)


def _seconds(milliseconds):
    return f"{milliseconds / 1000:.1f}s" if isinstance(milliseconds, (int, float)) else "n/a"


# ------------------------------------------------------------------------------- cli

def parse_models(spec: str | None) -> dict[str, str]:
    models = {}
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        role, _, model = part.partition("=")
        role = role.strip()
        if role not in ROLES or not model.strip():
            raise ValueError(f"Unknown role override {part!r}.")
        models[role] = model.strip()
    return models


def run_suite(suite: Path, arms, names, repeats, kind, settings, out_dir,
              max_cost_usd=None, models=None) -> dict:
    tasks = find_tasks(suite)
    if names:
        wanted = {name.strip() for name in names}
        tasks = [task for task in tasks if task.name in wanted]
        missing = wanted - {task.name for task in tasks}
        if missing:
            raise ValueError(f"Unknown task(s): {', '.join(sorted(missing))}")
    arms = tuple(arms)
    for arm in arms:
        if arm not in ARMS:
            raise ValueError(f"Unknown arm {arm!r}.")
    if kind == "openrouter" and not settings.api_key:
        raise ValueError("No API key. Set OPENROUTER_API_KEY or use --provider golden.")
    if kind == "openrouter" and max_cost_usd is None:
        raise ValueError("A run that can spend money needs --max-cost-usd.")

    run_id = time.strftime("%Y%m%d-%H%M%S")
    run_dir = Path(out_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    validation = []
    for task in tasks:
        entry = validate_task(task, run_dir / "validation" / task.name)
        validation.append(entry)
        state = "ok" if entry["valid"] else "PROBLEM: " + "; ".join(entry["problems"])
        print(f"validate {task.name}: {state}", flush=True)
    if kind == "golden" and any(not entry["valid"] for entry in validation):
        # A golden run exists to prove the checker accepts a correct answer; running
        # it against a broken checker would only produce a convincing-looking zero.
        raise ValueError("A task failed validation; refusing to run the golden control.")

    records, spent, stopped = [], 0.0, False
    for repeat in range(1, repeats + 1):
        for task in tasks:
            for arm in arms:
                if stopped:
                    continue
                print(f"run {task.name} [{arm}] r{repeat}", flush=True)
                record = run_cell(task, arm, settings, kind, run_dir, repeat, models)
                records.append(record)
                spent += record["cost_usd"] or 0.0
                mark = "solved" if record["solved"] else "not solved"
                spent_text = (f"${record['cost_usd']:.3f}"
                              if record["cost_usd"] is not None else "cost unknown")
                print(f"  {mark} in {_seconds(record['wall_ms'])}, {spent_text}", flush=True)
                if max_cost_usd is not None and spent >= max_cost_usd:
                    stopped = True

    results_path = run_dir / "results.jsonl"
    results_path.write_text("".join(json.dumps(record) + "\n" for record in records),
                            encoding="utf-8")
    run = {
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"), "provider": kind,
        "arms": list(arms), "tasks": [task.name for task in tasks], "repeats": repeats,
        "max_cost_usd": max_cost_usd, "stopped_at_cost_cap": stopped,
        "harness": (f"model={settings.model} max_tool_rounds={settings.max_tool_rounds} "
                    f"max_model_calls={settings.max_model_calls} "
                    f"max_repair_rounds={settings.max_repair_rounds}"),
        "models": models or {}, "validation": validation, "records": records,
    }
    run["summary"] = summarize(records)
    (run_dir / "run.json").write_text(json.dumps(run, indent=2), encoding="utf-8")
    (run_dir / "report.md").write_text(render_report(run), encoding="utf-8")
    (run_dir / "design.md").write_text(DESIGN_RUBRIC, encoding="utf-8")
    print(f"\nreport: {run_dir / 'report.md'}")
    return run


DESIGN_RUBRIC = """# Design rubric (operator, not automated)

The checkers grade behaviour they can execute. They cannot tell a game that is
correct from one that is pleasant to play, so that judgement stays here, separate,
and explicitly human. Fill this in after playing the result; do not fold it into
the solve rate.

| Cell | Clarity of goal | Controls feel | Camera | Visual cohesion | Would play again | Notes |
|---|---|---|---|---|---|---|

Score each column 1-5, or `n/a`. A run that solves every checker and scores 1 here
has produced working code, not a game, and the report should say so.
"""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m backend.bench",
        description="Run a task suite against Axiom's four-role graph and a "
                    "single-loop baseline, and grade both with an external checker.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="Prove each checker discriminates.")
    validate.add_argument("--suite", default=str(DEFAULT_SUITE))

    run = subparsers.add_parser("run", help="Run cells and write a report.")
    run.add_argument("--suite", default=str(DEFAULT_SUITE))
    run.add_argument("--arms", default="roles,single,null")
    run.add_argument("--tasks", default="")
    run.add_argument("--repeats", type=int, default=1)
    run.add_argument("--provider", default="openrouter",
                     choices=("openrouter", "golden", "void"))
    run.add_argument("--model", default=None)
    run.add_argument("--models", default=None, help="Per-role overrides: Coder=...,Reviewer=...")
    run.add_argument("--max-cost-usd", type=float, default=None)
    run.add_argument("--out", default=None)

    arguments = parser.parse_args(argv)

    if arguments.command == "validate":
        tasks = find_tasks(Path(arguments.suite))
        failed = False
        # Validation copies workspaces around; the task suite stays read-only.
        with tempfile.TemporaryDirectory(prefix="axiom-validate-") as scratch:
            for task in tasks:
                entry = validate_task(task, Path(scratch) / task.name)
                state = "ok" if entry["valid"] else "PROBLEM"
                print(f"{task.name}: {state}")
                for problem in entry["problems"]:
                    print(f"  - {problem}")
                failed = failed or not entry["valid"]
        return 1 if failed else 0

    settings = Settings.from_env()
    if arguments.model:
        settings = dataclasses.replace(settings, model=arguments.model)
    out = arguments.out or (str(Path.cwd() / "bench-results"))
    try:
        run_suite(Path(arguments.suite), arguments.arms.split(","),
                  [name for name in arguments.tasks.split(",") if name.strip()],
                  arguments.repeats, arguments.provider, settings, out,
                  max_cost_usd=arguments.max_cost_usd,
                  models=parse_models(arguments.models))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
