# HANDOFF.md — Current Axiom handoff

> The baton between coding sessions/models. Keep it short and current.
> Replace stale information instead of appending a diary.

## Current objective

Prove the new LangGraph runtime end to end: run one real multi-file task and confirm
that it reaches the Reviewer, that the repair edge fires when a review is rejected,
and that Coder actually delegates to a subagent.

## Intent

Axiom is a local control room where four agents plan, build, check and review work in
an isolated workspace. Recent runs failed on provider limits rather than on the task:
one spent 32 768 output tokens reasoning and wrote nothing, another wrote 13 files and
was cut at the round ceiling with Tester and Reviewer unused. Orchestration was
therefore replaced with LangGraph, so loops are explicit, bounded and resumable.
The remaining proof is a run that finishes and gets a verdict.

## Definition of done

- A task ends with `status: completed` and `review_verdict: approved`.
- Tester and Reviewer actually run; a Coder stop no longer cascades into three failures.
- Coder uses at least one delegated subagent on a multi-file task.
- The produced artifacts are executed *outside* the agent and the result is recorded.
- `.venv\Scripts\python.exe -m pytest backend/tests -q` passes.

## Current state

### DONE

- LangGraph runtime: `StateGraph` with per-role agent nodes, `validate`, `finalize`,
  and `route_after_review` as the repair edge (`backend/runtime.py`).
- Coder subagents in isolated workspaces: `backend/delegation.py`
  (`Delegation`, `OwnedWorkspace`, `delegation_tools`), capped at 3 workers and
  6 subagents per task.
- Shared project guidance and message compaction: `backend/coordination.py`
  (`DEFAULT_PROJECT_INSTRUCTIONS`, `compact_messages`).
- Continuing an earlier task from its existing workspace, keeping its model plan.
- Bounded repair: `AXIOM_MAX_REPAIR_ROUNDS=2` sends a `changes_required` verdict back
  to Coder instead of failing the task.
- Round accounting split: `AXIOM_MAX_TOOL_ROUNDS` counts rounds that *change* the
  project (write/edit/delegation); inspection is bounded by `AXIOM_MAX_MODEL_CALLS`.
- Credentials moved to `.axiom/credentials.env`, so saving the key in the dashboard no
  longer makes `next dev` reload the page.
- Per-agent models, truncation recovery, empty-response retries, output-limit
  reporting and rejected-call attribution.
- 67 tests pass.

### IN PROGRESS / PARTIAL

- **Superseded 2026-09-16 — the runtime has now run end to end.** `744af3f3` (Varg i
  hagen) finished `completed` with `review_verdict: approved`, 38 model calls and 0
  repair rounds. `4e5f4e80` (Zombiegata) reached Reviewer, was rejected, exercised the
  repair edge (`repair_round: 1`) and then failed on a Coder repeat-loop rather than on a
  limit. What is still unexercised is delegation: `workers` is empty for every task, so
  no run has used a Coder subagent yet. See `HANDOFF-FOR-ASTRA.md` for the full table.
- Scope drift is not enforced anywhere. The Coder role prompt deliberately allows
  dependency manifests and build configuration when the product needs them (ADR-011),
  but a task that *forbids* them is not checked, and one run added `package.json` and
  `serve.py` to a dependency-free request.
- Real verification — actually executing generated code — is still done by hand,
  outside the pipeline.

### NEXT

1. ~~Run one multi-file task to completion. Confirm `route_after_review` fires at least
   once and that a subagent is used.~~ **Done for the first two, open for the third:**
   `744af3f3` completed with an approved verdict and `4e5f4e80` fired the repair edge, but
   no run has yet delegated to a Coder subagent.
2. Execute the produced artifacts outside the agent (`node tests/...`) and record the
   result in the task summary.
3. Only then revisit the round, time and token defaults.

## Files changed

Relative to `f6133c3`. This session's pull brought `a56c14e`..`c6f4a7a`.

- `backend/runtime.py` — rewritten around a LangGraph `StateGraph`: agent nodes,
  validation, finalize and repair routing.
- `backend/delegation.py` — new; Coder subagents, owned workspaces, content digests.
- `backend/coordination.py` — new; shared project instructions and message compaction.
- `backend/app.py`, `backend/config.py` — new knobs, credentials handling, request validation.
- `backend/workspace.py` — extended checks (`edit_file`, more validated suffixes).
- `app/page.tsx`, `app/globals.css` — per-agent models, instruction panel, pipeline label.
- `backend/tests/test_continuation.py`, `test_coordination.py`, `test_delegation.py` — new suites.
- `AGENTS.md`, `DECISIONS.md`, `HANDOFF.md`, `README.md` — continuity contract and docs.

## Important decisions made in this session

Recorded as ADR-001..ADR-011 in `DECISIONS.md`. Read **ADR-001** (LangGraph is the
runtime, not a hand-rolled loop), **ADR-002** (the round budget counts changes, not
inspection) and **ADR-011** (dependency manifests are allowed when the product needs
them) before touching orchestration, limits or Coder instructions.

## Known issues / failing tests

1. **Explicit task constraints are not enforced.** The Coder role prompt allows
   dependency manifests and build configuration when the product needs them
   (ADR-011), but nothing verifies a task that forbids them. Run `f4dbba1f` added
   `package.json` and `serve.py` to a request that specified a dependency-free page
   openable from `index.html`. Do **not** "fix" this by banning manifests globally —
   that reverses a deliberate decision. If it matters, make it a per-task check.
2. `HANDOFF.md` shipped as an empty template. It is filled now; keep it that way,
   because `AGENTS.md` requires the next session to read it first.
3. Old failed tasks and their workspaces remain in `.axiom/tasks.sqlite3` and
   `.axiom/workspaces/`. They are evidence, not junk. Do not delete them unasked.
4. `.env` is developer-local and gitignored, and it **overrides the code defaults**.
   That already caused drift once: it pinned `AXIOM_MAX_TOKENS=16384` while the new
   default is 32768. Check it before debugging a limit.
5. No failing tests.

## Do not change accidentally

- Permissions are enforced by tools, not prompts: only Coder writes; Planner, Tester
  and Reviewer inspect and report. Generated guidance must not grant capabilities.
- The repair loop stays bounded, and an approval must never override a deterministic
  validation failure.
- Static parsing, real execution and visual checks stay distinguishable in the API and
  in UI labels. Never describe static-only output as runtime-tested.
- Credentials precedence: process environment > `.env` > `.axiom/credentials.env`.
- The runtime proxy accepts loopback origins and rejects remote or opaque origins that
  are not same-site. That was a real bug; do not simplify it back to string equality.
- The "This is NOT the Next.js you know" block at the top of `AGENTS.md` is regenerated
  by `next dev`. Commit it with your work rather than deleting it.

## Verification performed

- [x] Relevant tests — 67 passed
- [x] `langgraph` installed into the venv; backend starts and serves `/api/health`
- [x] Dashboard and backend both answer 200 on loopback
- [ ] Live end-to-end run (the whole point of NEXT)
- [ ] Generated artifacts executed

Commands/results:

```text
.venv\Scripts\python.exe -m pytest -q     -> 67 passed, 1 warning in 28.23s
GET http://127.0.0.1:3000/api/runtime/health -> {"configured":true,...,"status":"ok"}
GET http://127.0.0.1:8000/api/health         -> HTTP 200
```

## Git state

Branch: `main`

Last relevant commit: `c6f4a7a fix: a continuation keeps the earlier model plan`

Working tree: clean (`.env` is gitignored and always local).

Consider a checkpoint commit before the next session, per `AGENTS.md`:
`checkpoint: LangGraph delegation handoff`.

## Notes for the next agent

Before editing:

1. Read `AGENTS.md`.
2. Read this file.
3. Run `git status`.
4. Inspect relevant diff/history.
5. Verify this handoff against the actual repository.

Then summarize: `DONE / PARTIAL / NEXT`.

Continue from `NEXT`; do not restart completed work.

The local servers are session state, not repository state. Backend:
`.venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000`,
run from the repository root so `.axiom/` resolves correctly. Dashboard:
`npm run dev:local`, serving `http://127.0.0.1:3000` and proxying `/api/runtime/*`
to port 8000.
