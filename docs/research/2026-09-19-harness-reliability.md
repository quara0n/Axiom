# Axiom: reliability implementation and path to a stronger harness

Baseline: `e149633` on `codex/axiom-harness-efficiency`, including JEV and MCP.
Implementation branch: `codex/axiom-frontier-foundation`.
The earlier draft against `4ccd03e` was left separate when Rune reported newer code.

## What the current code establishes

The harness had useful architecture and tests already. Its failures were not all
missing components: several were incorrect decisions around evidence and stopping.
This change addresses those observable failures. It does not establish a new score
out of ten, frontier parity, a cheaper live run, or better game design.

JEV exists as `backend/jev.py`, configuration, an offline test suite and a probe.
Neither the graph nor the agent loop currently calls it. The previous handoff
records a small SimpleJev experiment, including confident answers without enough
evidence. That supports keeping deterministic evidence gates independent of JEV;
it does not establish calibrated correctness probabilities for the TypeSafe model.

## Changes against the twelve-point assessment

| Area | Implemented in this branch | Remaining limitation |
| --- | --- | --- |
| Tool routing | Worker inspection/integration only offered while a completed proposal exists; delegation removed at quota. Actual dispatch enforces role and selected menu, including MCP. | No semantic file ranking or model router. File count alone is not a good reason to hide useful file tools. |
| Retries | Reporting roles default to an 8192-token ceiling. A truncation recovery is capped at 4096 output / requested 512 reasoning. Provider reasoning compatibility is per model. Retry-After waits are not shortened by jitter. | Provider support varies; requested reasoning settings are not a guarantee. An excessive wait stops with a resumable explanation. No automatic provider/model spending escalation. |
| Handoffs | Extractive excerpts favor findings relevant to the receiving role, retain beginning/conclusion and source offsets. Exact report pages can be retrieved. Full completed reports retained. | Extraction uses English keyword heuristics, not semantic summarization. Agent must retrieve omitted evidence when needed. |
| State | Static failures route around execution. Executed failures remain blockers even with an approved review. Files altered by executed checks are parsed again. | Four role nodes remain. Continuation is a new pass, not checkpoint replay. |
| Memory | A continuation can retrieve the original parent reports by exact pages. | No general cross-task memory store. Unfiltered old failures should not become instructions. |
| Sandboxing | Existing opt-ins retained. Explicitly constrained tasks cannot use unconfined MCP or execution to bypass file contracts. | No container/OS sandbox added. Existing external processes and browser previews remain outside such a boundary. |
| Permissions | Exact allowed/forbidden file contracts enforced at direct writes, edits, delegation, integration and output validation. Inherited by continuation. | API contract is explicit; prose constraints are not automatically compiled. No new dashboard contract editor. |
| Roles | Independent inspection remains; retrieving a team report does not satisfy workspace-evidence requirements. | Roles are not skipped by JEV. Skipping needs task-level evaluation first. |
| Caching | Fresh verification-stage parse; cached inspection results include hash, parser version and original parse time. | Cached parsing is not itself a defect when the inputs match. This does not implement semantic/answer caching. |
| Subagents | Existing two-worker delegation/integration tested alongside new capability and scope boundaries. Inspection no longer consumes a write round. | Scripted models exercise machinery, not live delegation quality. |
| Termination | Warning at four repeated observations, two bounded corrective refusals, stop at seven. Reads reset after real changes/compaction. Near-deadline nudge added; hard budgets retained. | The nudge cannot guarantee completion before timeout. There is no automatic infinite continuation or unsafe mutation replay. |
| Observability | Bounded per-tool sequence, duration, outcome, argument digest and output size; monotonic usage IDs beyond the retention window. Existing benchmark reused; cost/tokens per solve include failed attempts and refuse incomplete totals. | No distributed tracing service; argument bodies deliberately excluded. No measured live cost/quality gain. |

## Evidence

Baseline suite: **170 passed**. Updated suite: **208 passed**, including 38 added
fault-injection/regression cases; one existing third-party Starlette deprecation
warning. Python tests run from the updated worktree. No frontend files changed.

```sh
python -m pytest backend/tests -q
python -m backend.bench run --provider golden --arms roles,single,null --model test/golden --out bench-results/golden
python -m backend.bench run --provider void --arms roles,single --model test/void --out bench-results/void
```

The benchmark validates all four checkers first: each rejects its untouched
workspace and accepts its known solution. Control runs recorded on 2026-09-20 UTC:

| Control | Four-role arm | Single arm | Null arm |
| --- | --- | --- | --- |
| Golden provider writes known solutions | 4/4 solved | 4/4 solved | 0/4 solved |
| Void provider does no work | 0/4 solved | 0/4 solved | Not run |

This proves the evaluation plumbing discriminates. It is not a model benchmark.
No paid provider calls or external project data uploads were made. Neither an
OpenRouter key nor a TypeSafe key was configured in this execution environment.

Specific regressions cover failed execution overriding approval, later manual
failure revoking completion, changed output reparsing, cache provenance, truncated
approval rejection, recovery request limits, repeated read recovery, repeated
validation of changed files, late handoff findings, exact parent-report retrieval,
forged MCP calls from non-Coder roles/workers, task file contracts, worker menu
transitions, deadline warning, trace redaction, monotonic IDs and failed-attempt accounting in cost/tokens per solve.

## Recommended next work, in order

1. **Run a fixed live comparison.** Use the same task prompts, starting files,
   role models, provider routes and explicit cost ceiling for baseline `e149633`
   and this branch. The existing benchmark cost threshold is checked between cells,
   so it can overshoot within a task; use a provider-side spend limit when a hard
   monetary ceiling is required. Repeat tasks; report solved/attempted, false approvals, actual
   cost per solved task, median and tail time, termination cause, tool calls and
   token/cache totals. Keep failed runs in the denominator. An independent checker
   decides solved; internal approval does not. Four known tasks alone are too
   small and too familiar to establish frontier performance.
2. **Add isolated execution and browser evidence.** Make process termination and
   output ownership reliable, including descendants after cancellation. Test a
   playable game in a browser: spawn, controls, collisions, progression, restart,
   layout at the target viewport. A build or source read cannot prove those visual
   and interactive outcomes. Keep host filesystem/network boundaries explicit.
3. **Evaluate JEV in shadow mode.** Start with ranking which evidence to inspect
   next and flagging potential stagnation. Log version, state digest, evidence
   availability, decision distribution, latency and usage beside the action the
   existing harness took. Do not use its confidence as a calibrated probability.
   Missing evidence means abstain in harness code, not 'ask JEV to be certain'.
   Measure disagreement and resulting quality before letting it skip any role.
4. **Add structured acceptance criteria and handoffs.** Give criteria stable IDs;
   attach changed files, unresolved blockers, next action and evidence references.
   Keep the original brief and deterministic gates authoritative. The planner may
   propose a contract; it must not silently weaken one supplied by the caller.
5. **Add replay-safe checkpoints and curated project memory.** Persist operation
   intent/result and artifact digests before resuming side effects. Keep memory
   project-scoped and evidence-linked; begin with exact/lexical retrieval and
   measure whether semantic retrieval adds value. Do not equate stored transcripts
   with learning, or embeddings with a stronger harness.

These steps can be evaluated separately. A more elaborate graph or more agents is
not itself a success criterion. Reliability and solved-task cost are.

## Design references

Incremental work and durable handoff artifacts are consistent with Anthropic's
[long-running harness experiments](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents).
Their examples also stress end-to-end checks before claiming completion; they do
not establish that a multi-agent architecture is always better.

Task-based tool evaluation and token-efficient tool results are also discussed in
[Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents).
This branch's concrete choices are justified by Axiom's code and regression tests,
not by assuming that another provider's measurements transfer to Axiom.
