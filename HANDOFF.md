# HANDOFF.md — Current Axiom handoff

> This file is the baton between coding sessions/models.
> Keep it short and current. Replace stale information instead of appending a long diary.

## Current objective

[What are we implementing right now?]

## Intent

[Why does this work exist? What outcome is the user trying to achieve?]

## Definition of done

- [Observable condition 1]
- [Observable condition 2]
- [Tests/behavior that must pass]

## Current state

### DONE

- [Completed item]

### IN PROGRESS / PARTIAL

- [Partially implemented item]
- [What is missing]

### NEXT

1. [Exact next action]
2. [Following action]

## Files changed

- `path/to/file.py` — [what changed and why]

## Important decisions made in this session

- **Decision:** [decision]
  - **Why:** [reason]
  - **Do not reinterpret as:** [common mistaken interpretation, if useful]

If a decision should survive beyond this task, also add it to `DECISIONS.md`.

## Known issues / failing tests

- [Issue or test]
- [Expected vs actual behavior]

## Do not change accidentally

- [Architecture/interface/behavior that must be preserved]
- [WIP code that may look removable but is intentional]

## Verification performed

- [ ] Relevant tests
- [ ] Compile/type/lint checks
- [ ] Manual runtime check
- [ ] `git diff` reviewed

Commands/results:

```text
[short verification output]
```

## Git state

Branch: `[branch]`

Last relevant commit/checkpoint: `[hash/message if known]`

Working tree:
- [clean / uncommitted changes present]
- [important uncommitted files]

## Notes for the next agent

Before editing:
1. Read `AGENTS.md`.
2. Read this file.
3. Run `git status`.
4. Inspect relevant diff/history.
5. Verify this handoff against the actual repository.

Then summarize:
`DONE / PARTIAL / NEXT`

Continue from `NEXT`; do not restart completed work.
