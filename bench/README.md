# Benchmark

Axiom's task store records what the team said about itself, and the Reviewer that
approves a run is part of the same run. That makes `approved` a claim, not a
measurement. This directory is the other half: tasks with a grader the harness
cannot edit, a control that must fail, and one headline number, **cost and
wall-clock per solved task**.

No published harness number is comparable with these cells. Different tasks,
different graders and different models make cross-benchmark comparison
meaningless - including with HarnessTax and OpenBench, which this design follows.
Only cells from the same run are comparable with each other.

## Running it

```sh
# 1. Prove every checker discriminates. Costs nothing.
python -m backend.bench validate --suite bench/tasks

# 2. Self-test the pipeline with no model and no spend: the golden solution must
#    be solved by every arm that can solve, and the void control must solve none.
python -m backend.bench run --provider golden --arms roles,single,null \
    --out bench-results --model test/golden
python -m backend.bench run --provider void --arms roles,single \
    --out bench-results --model test/void

# 3. The real run. A provider that can spend requires an explicit cap up front.
python -m backend.bench run --provider openrouter --arms roles,single \
    --tasks three-d-game --repeats 3 --model openrouter/free \
    --max-cost-usd 5 --out bench-results
```

Each run writes `results.jsonl` (one line per cell), `run.json`, `report.md` and
an empty `design.md`. `--models Coder=...,Reviewer=...` overrides a role model.

## What a task is

```
bench/tasks/<name>/
  task.json      the prompt, and an optional timeout in seconds
  workspace/     the starting files, copied into the cell
  checker/       the grader - never copied into the workspace
  solution/      a golden answer, used to prove the checker accepts a correct one
```

The checker receives the workspace path, runs however it likes, and prints
`SCORE: <passed>/<total>` for partial credit. **Exit code 0 is the only thing that
counts as solved**, so a partial result is visible without being rounded up.

Two rules make the number mean something:

- The harness never sees `checker/` or `solution/`. A run that can read its own
  grader is not being graded.
- `validate` runs both controls before any cell: the checker must **reject the
  untouched workspace** and **accept the golden solution**. A checker that accepts
  everything scores every harness as perfect; one that rejects the solution makes
  every harness look broken. Both fail silently without this step.

## The arms

| arm | what it is |
|---|---|
| `roles` | The shipped pipeline: Planner, Coder, static checks, Tester, Reviewer, repair rounds. |
| `single` | The Pi-class baseline: one context, a short instruction block, the Coder's tools, no delegation, no internal verdict. |
| `null` | The negative control: no model calls at all, the workspace untouched. |

`single` and `roles` share the workspace boundary, the usage ledger, the loop
guards and the budgets, so a difference between them is about the number of
contexts and the length of the instructions - not about tools. A test asserts the
two tool surfaces stay identical; if the Coder gains a tool, the baseline has to
keep up or the comparison would be rigged.

## Reading a report

The report gives solve rate with a Wilson 95% interval, median wall-clock, median
cost, cost per solve, prompt tokens per solve and median model calls, per arm.
With a handful of cells the interval is wide: treat two arms as different only
when their intervals separate, and raise `--repeats` before believing a small
gap.

The `Did the harness know?` table is the honesty check. `approved` is Axiom's own
verdict and `solved` is the external checker; every `**no**` is a run that
believed something untrue.

## What this does not measure

Visual and design quality. The checkers grade behaviour they can execute. The
`three-d-game` task fixes a simulation contract in `src/sim.js` precisely so that
driving, collision, checkpoint order and restart can be graded headlessly - and
that means it says nothing about whether the game looks or feels good. That
judgement stays in `design.md`, filled in by a person, and is deliberately never
folded into the solve rate.

Cost accounting is only as good as the provider's report. A cell whose provider
returned no usage records `cost unknown` and counts as an unknown call, so a run
can never look cheaper than it was.
