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

JEV is still `backend/jev.py`, configuration, offline tests and `scripts/jev_probe.py`;
it is not connected to the runtime loop. Preserve this existing work. The prior
handoff recorded 6/6 known-answer hits from a SimpleJev demo, plus confident answers
without evidence. That is not calibrated TypeSafe-model validation. Details remain
in `docs/research/2026-09-19-jev-system-one-in-axiom.md`.

Still unimplemented/unmeasured: live comparative quality and cost, live subagent
quality, container isolation/process-tree cancellation, browser interaction checks,
JEV shadow evaluation and routing, structured acceptance criteria, durable safe
checkpoint replay and broader project memory. No frontend changes in this branch.

## Verification

- Baseline: 170 passed. Updated: **208 passed**, 38 new regression cases; one
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
