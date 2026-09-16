<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

# Axiom runtime development

- The Python agent runtime is in `backend/`; the dashboard is in `app/`.
- Keep agent permissions enforced by tools, not only by prompts. Coder owns code
  writes; other roles inspect and report. Generated AGENTS.md guidance must not
  grant extra filesystem or execution capabilities.
- Keep repair loops bounded and preserve reports for each pass. An approval must
  not override deterministic validation failures.
- Distinguish static parsing, actual execution and visual checks in API results
  and UI labels. Never describe static-only output as runtime-tested.
- Verify runtime changes with `python -m pytest backend/tests -q` in the project
  virtual environment. Verify dashboard changes with TypeScript and ESLint.
- Shared instructions for generated projects live in `backend/coordination.py`;
  this repository's Next.js rules do not belong in every generated project.

---

# Multi-model continuity and handoff

## Purpose

Axiom is developed by multiple coding agents and LLM providers (for example Astra and DeepSeek).
The goal of this file is to preserve engineering intent across model/session changes.

This file is a permanent project contract. Do not rewrite it for each session.

---

## Required startup routine

Before making any implementation change, the agent MUST:

1. Read `AGENTS.md`.
2. Read `HANDOFF.md` if it exists.
3. Run `git status`.
4. Inspect relevant recent diffs/history.
5. Read the files directly involved in the task.
6. Search for an existing implementation before creating a parallel one.
7. State a short internal working model of:
   - CURRENT INTENT
   - CURRENT STATE
   - NEXT ACTION

The repository and runtime behavior are the source of truth.

If `HANDOFF.md` conflicts with the code, verify the code before proceeding.

---

## Source-of-truth order

When information conflicts, use this order:

1. Current repository state
2. Tests / runtime behavior
3. Explicit current user instruction
4. `AGENTS.md`
5. `DECISIONS.md`
6. `HANDOFF.md`
7. Recent git history
8. Assumptions from a previous agent/session

Do not preserve an old plan when the user has explicitly changed direction.

---

## Continuity rules

When taking over work from another model/session:

- Continue from the existing implementation; do not restart from scratch.
- Do not redesign working architecture merely because you prefer another approach.
- Preserve existing naming, module boundaries, and interfaces unless there is a concrete reason to change them.
- If a redesign appears necessary, explain the reason before performing it.
- Distinguish:
  - completed work,
  - partially completed work,
  - experimental/WIP work,
  - broken or obsolete work.
- Never discard uncommitted changes without explicit user approval.
- Never run destructive Git operations (`reset --hard`, destructive checkout/restore, force pushes) without explicit user approval.

---

## Intent preservation

For every non-trivial task, identify these four things before implementation:

### Goal
What user-visible or system-level outcome is intended?

### Constraints
What must remain true while implementing it?

### Existing decisions
What architectural choices have already been made that affect this task?

### Definition of done
What observable conditions mean the task is complete?

If any of these are unclear and the ambiguity could materially change the architecture, ask before making a large change.

---

## Implementation behavior

- Prefer small, reversible changes.
- Reuse existing abstractions before adding new ones.
- Avoid unrelated refactors during feature work.
- Do not silently change architecture, provider strategy, persistence model, APIs, or agent responsibilities.
- Do not introduce a second implementation of the same responsibility unless intentionally migrating.
- Keep interfaces between orchestrator, agents, tools, model providers, runtime, and persistence explicit.

---

## Verification

Before declaring work complete:

1. Run relevant tests.
2. Run targeted lint/type/compile checks if available.
3. Inspect `git diff`.
4. Confirm that the implemented behavior matches the original intent.
5. Report any known limitations or unverified assumptions.

---

## Handoff protocol

Before ending a substantial work session or switching LLM/provider:

Update `HANDOFF.md`.

The handoff must contain:

- Current objective
- Current intent / why this work exists
- What was completed
- Files changed
- Important decisions and WHY
- What is partially implemented
- Known failures / failing tests
- What remains
- Exact recommended next action
- Things the next agent must not accidentally change
- Verification performed
- Git state

Keep the handoff concise. It is current-state context, not a diary.

If a decision should remain relevant after the current task is finished, record it in `DECISIONS.md`, not only in `HANDOFF.md`.

---

## Git checkpoints

For meaningful model/session handoffs, prefer a checkpoint commit when practical.

Example:

`checkpoint: LangGraph delegation handoff`

A checkpoint commit does not mean the feature is finished. It marks a recoverable boundary between agents/sessions.

If work is intentionally left uncommitted, the next agent must inspect `git status` and `git diff` before editing.

---

## Model/provider switching

Astra, DeepSeek, or another model may be used as the reasoning engine while the same coding harness operates on the repository.

Do not assume a new model has access to the prior chat context.

Persist important context in:
- code,
- tests,
- `DECISIONS.md`,
- `HANDOFF.md`,
- Git history.

Chat history is supplemental, not authoritative.
