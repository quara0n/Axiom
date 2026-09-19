# HANDOFF.md — Current Axiom handoff

## Objective and intent

Rune asked for concrete improvements toward a frontier harness, starting with a
12-point audit. Work uses the latest pushed JEV/MCP code (`e149633`), not the older
GitHub snapshot. Fix false completion, weak recovery and untrustworthy measurements
before adding probabilistic routing. No frontier score or live cost gain is claimed.

## Completed

- Deterministic static/scope failures skip execution; actual execution failures
  override Reviewer approval. Reparse sources altered by an executed check.
  A build alone is not marked runtime-tested. Later failed manual checks revoke
  completion; active tasks cannot be manually executed.
- Enforce selected tool capabilities at dispatch, including Lead-Coder-only MCP.
  Worker tools are offered when proposals exist; delegation disappears at quota.
- Per-call reporting limits (default 8192); one truncation recovery capped at
  4096 output / requested 512 reasoning. Provider compatibility is per model.
  Retry-After is never shortened. Shared providers are not mutated by call policy.
- Repeated calls get feedback before stopping: warning at four, corrective
  refusals at five/six, hard stop at seven. Real changes/compaction reset read
  observations; work/model/repair/time bounds remain hard. Near-deadline nudge.
- Extractive handoffs with offsets and exact paginated current/parent report
  retrieval. Preserve full reports; retrieved reports do not satisfy independent
  evidence requirements.
- Explicit allowed/forbidden file contracts at writes, edits, delegation,
  integration and final verification; inherited by continuation. Constrained tasks
  cannot use unconfined MCP or execution. Prose constraints remain advisory.
- Fresh verification parsing; cached inspection has hash/version/time provenance.
  Bounded per-tool timing/outcome/argument-digest traces and monotonic usage IDs.
- Benchmark cost/tokens per solve include failed attempts. Incomplete usage is
  reported as unavailable instead of an artificially low complete total.

## Files and decisions

New `backend/control.py`, `backend/constraints.py`, and
`backend/tests/test_harness_control.py`. Updated runtime, single-loop benchmark arm,
provider/config, workspace, coordination, delegation, continuation, task API and
benchmark summaries/tests. README, `.env.example`, ADR-014 in `DECISIONS.md` and
`docs/research/2026-09-19-harness-reliability.md` document behavior and limitations.

Keep LangGraph, SQLite snapshots, role ownership, bounded repairs, and explicit
static/executed/visual evidence. Do not reintroduce model approvals overriding
failed checks or dispatch permissions enforced only by schema visibility.
Do not claim file contracts are an OS sandbox. Do not replay mutations to recover.

## JEV and remaining work

JEV is now connected, narrowly and on purpose. `backend/jev_evidence.py` runs inside
the `validate` node after the static checks and records its findings as
`verification.jev_evidence` for the Tester and Reviewer. It takes the requirements the
task states itself (bullet lines first, then sentences carrying a requirement word,
capped at eight), sends the files the run wrote as one state (entry point first, 20k
character budget, truncations and omissions named), and asks one noul question per
requirement.

Three properties matter for review. It is evidence and never a verdict: it cannot
approve a task and cannot override a failed check. Only a confident no is a finding:
below 0.35 is `contradicted`, at or above 0.85 is `supported`, everything else is
`unclear`, because JEV answers even when the evidence is missing and a value near 0.5
carries no information. And any failure is recorded as `unavailable` instead of being
raised, so gathering evidence can never fail a task. Thresholds and the budget are
module constants, and every finding keeps its raw probability so a calibration pass on
our own tasks can move them. It runs only when a TypeSafe key is configured and
`AXIOM_JEV_EVIDENCE` is on, and costs about 8.3k input tokens (~$0.00035) for a
one-file game.

Measured against the real model (`jev-1.13.0`, live key) on two finished artifacts:
20/23 on claims plain code can decide, against 22/23 for the free SimpleJev endpoint on
the same claims. JEV was 2-3x faster and used roughly 30 % fewer input tokens. Both
were wrong on the two questions that needed the artifact to run, and JEV marked
requirements `supported` (0.90-0.94) that the Reviewer's hand analysis found real
defects in. That is why only a confident no counts, and why a JEV answer never outranks
code. Details and sources: `docs/research/2026-09-19-jev-system-one-in-axiom.md`.

The evidence pass was then tightened, because its first version could turn its own blind
spots into defects. It now separates three things: the model's raw answer, the evidence
the answer rests on, and the claim we are willing to make. A requirement about behaviour
- controls, collisions, visibility, sound - is `insufficient_evidence` however confident
the answer, because reading source cannot establish behaviour in either direction. A
requirement readable from source is `contradicted` only when the evidence was complete;
with a file omitted or truncated, a low answer stays `insufficient_evidence`. Values
that are not probabilities (NaN, infinity, outside 0-1) are rejected outright, the
truncation marker counts inside the state budget, and the truncations and omissions are
disclosed to JEV in the request. The role prompt now calls a negative finding a
hypothesis to investigate rather than a defect to disprove.

Calls are bounded and recorded: one configurable wall-clock budget per pass covering
retries and backoff, no client and no request for the hosted endpoint without a key
(an explicitly configured local endpoint is still allowed without one), an identical
basis reused by content fingerprint instead of re-asked, and a per-task ledger
(`value["jev"]`) carrying purpose, model, latency, tokens, status and reuse across
repair rounds. Cost is estimated from tokens and marked as estimated; an unpriced call
is recorded as unknown, never as zero. `validate` runs again after a repair, so this is
a pass per validation, not one call per task.

Shadow mode (`AXIOM_JEV_SHADOW=1`, off by default) records what JEV would recommend
next and never acts on it. It is triggered only by the existing repeat guard's
no-progress signal - no second detector - offers only actions the current role and
harness actually allow, is capped per task and time-limited, and stores each
recommendation next to what the workflow actually did, so a "followed" rate can be
measured later. It changes no permission, tool menu, role or verdict.

Still unimplemented/unmeasured: JEV-driven routing and tool decisions, a calibration
study on our own labelled tasks, live comparative quality and cost, live subagent
quality, container isolation/process-tree cancellation, browser interaction checks,
structured acceptance criteria, durable safe checkpoint replay and broader project
memory. No frontend changes in this branch.

## Verification

- Baseline: 170 passed. Updated: **238 passed**, 68 new regression cases (38 from the
  harness patch, 30 covering the JEV evidence assessment and shadow mode); one
  existing third-party Starlette deprecation warning.
- `python -m pytest backend/tests -q` in the project environment. This session used
  `NO_PROXY=127.0.0.1,localhost,::1` for loopback tests behind the environment proxy.
- Golden benchmark: four tasks, roles 4/4, single 4/4, null 0/4.
- Void benchmark: roles 0/4, single 0/4. All four checkers first rejected untouched
  inputs and accepted their known solutions (runs dated 2026-09-20 UTC).
- No paid calls: no OpenRouter or TypeSafe key configured here. No frontend build
  or browser validation. No known failing tests.

## Recommended next action

Review this branch against `codex/axiom-harness-efficiency`. Then run the fixed live
suite on baseline `e149633` and this branch using identical model selections,
starting inputs and repetition counts. With the chosen model/key configured, use:

```sh
python -m backend.bench run --provider openrouter --arms roles,single,null --repeats 3 --max-cost-usd 5 --out bench-results/live
```

This is a proposed command, not a run already performed. The monetary threshold is
checked BETWEEN cells and can overshoot within a task; use a provider-side limit
for a hard spend ceiling. Keep failed runs and unknown usage in the results.
Evaluate broader held-out tasks before making any frontier-performance claim.
Next engineering step is isolated executable/browser evidence, then JEV shadow
mode with evidence gating; see the research assessment for the evaluation plan.

## Git state

Implementation branch: `codex/axiom-frontier-foundation`, based on `e149633` from
`codex/axiom-harness-efficiency`. Implementation is committed locally. Rune has
explicitly authorized committing and pushing these changes to the repository.
Publication is blocked by access: terminal Git has no HTTPS credentials, and the
connected GitHub integration returned HTTP 403 (Resource not accessible by
integration) when creating the tree. No branch or PR was created. After repository
write access is restored, publish this separate branch and a draft PR against the
efficiency branch; do not merge main. Check remote/PR status before retrying.
The previous uncommitted draft against `4ccd03e` remains untouched in the sibling
`axiom` worktree on `codex/axiom-reliability-control`. Do not integrate that stale
patch over this branch. Inspect status/diff before continuing any work.
