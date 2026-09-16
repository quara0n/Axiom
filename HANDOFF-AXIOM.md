---
artifact_contract: "ce-handoff/v1"
created_at: "2026-09-16T20:12:01Z"
title: "Axiom repository briefing for an incoming agent"
summary: "The Axiom product repo, its LangGraph runtime, the run history that proves it works, and the open threads — enough for a fresh agent to take over without the session transcript."
keywords: ["axiom", "langgraph", "agent-orchestration", "openrouter", "codex-handoff", "agent-workflow-eval"]
cwd: "C:/Users/runef/Downloads/Axiom"
repository: "quara0n/Axiom"
repo_root_sha: "b2a6fa1264d7d144d8af4f4f6dd2dd694dbd68ec"
branch: "main"
head: "49cb17cc7d8e2d81df1069bd6df0a81175021bf0"
worktree_path: "C:/Users/runef/Downloads/Axiom/github-local"
---

# Axiom — repo briefing for an incoming agent

Written 2026-09-16 at the end of a long Codex session. Everything marked *verified* was
executed or measured on this machine today. This document is the writer's account: where
it says the user wanted something, that is the user's stated intent; everything else is
the writer's reading.

## 0. The short answer

Axiom is a local control room for a four-agent coding pipeline (Planner → Coder →
Tester → Reviewer, plus a bounded repair edge) that runs on OpenRouter and writes into
an isolated workspace per task. The product is finished enough to be useful: it has
completed a real multi-file build and got a clean verdict from its own reviewer. Its
weakest link is not orchestration any more — it is that nothing in the pipeline ever
*executes* what it produced, so every defect that mattered was found by a human playing
the game afterwards.

## 1. Where everything lives — read this before touching anything

There are **three git repositories** on this disk and one of them is not a backup.

| Path | What it is | State |
|---|---|---|
| `github-local/` | **The product.** Dashboard + Python runtime. Remote `https://github.com/quara0n/Axiom`, branch `main`. | Clean, 20 commits, HEAD `49cb17c` |
| `project/` | An earlier clone of the same GitHub repo (branch `codex/axiom-agent-runtime`, HEAD `6c4ae0b`). Its `origin` is a local folder, its `github` remote is the same repo. | Clean; `6c4ae0b` is an ancestor of `github-local`'s `main` |
| the repo root `Axiom/` | A `.git` with **zero commits and no remote** | Everything at the root is untracked |

Consequence, and it is the most important operational fact here: **`research/`, `.run/`
and `test-temp/` are not tracked by any repository.** They exist only on this machine.
That includes the 43 KB decision paper `research/AIDER-VS-LANGGRAPH.md` and its diagram,
and every prompt, log and screenshot from the game runs. If the machine is lost, so are
they. If the next agent needs them, they must be copied or published deliberately —
`git -C github-local add ../research` is not a thing that works.

The absolute paths below are machine-local and are labelled as such.

## 2. How to run it

From `github-local/` (Python 3.11+, Node):

```sh
python -m pip install -e ".[test]"
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000      # backend
node node_modules/next/dist/bin/next dev --hostname 127.0.0.1 --port 3000
python -m pytest backend/tests -q
```

Dashboard: <http://127.0.0.1:3000>. The backend API is plain REST and is the fastest way
to inspect state: `GET /api/health`, `GET /api/tasks`, `GET /api/tasks/{id}`,
`POST /api/tasks`, `POST /api/tasks/{id}/cancel`.

Two environment notes that have already cost time:

- **The backend must be started outside the editor's sandbox on this machine.** The venv
  is a `uv` trampoline and dies with `uv trampoline failed to spawn Python child process`
  when launched from inside it. The same applies to `pytest`.
- **Credentials.** A key entered in the dashboard is written to `.axiom/credentials.env`,
  not `.env` — `next dev` watches `.env*` and reloading mid-save left the dialog stuck on
  "Saving connection…". Precedence is process environment > `.env` > `.axiom/credentials.env`.
  `.env` is developer-local and gitignored, and it *overrides the code defaults*, which
  has already caused drift (it pinned `AXIOM_MAX_TOKENS=16384` while the default is 32768).

## 3. Architecture in one screen

`backend/runtime.py` compiles a LangGraph `StateGraph`:

```text
START -> Planner -> Coder -> static checks -> Tester -> Reviewer -> decision
                      ^                                             |
                      +------------ repair findings ----------------+---> END
```

The parts that carry the design, with what matters in each:

- `backend/runtime.py` — the graph, the agent nodes, `validate`, `finalize` and
  `route_after_review` (the repair edge). Read this first.
- `backend/delegation.py` — Coder subagents in owned workspaces, capped at 3 workers and
  6 subagents per task. `Delegation`, `OwnedWorkspace`, `delegation_tools`.
- `backend/coordination.py` — `DEFAULT_PROJECT_INSTRUCTIONS` (the text every agent gets)
  and `compact_messages`.
- `backend/workspace.py` — the tool surface and path/extension validation.
- `backend/config.py` — the knobs: `AXIOM_MAX_REPAIR_ROUNDS` (default 2),
  `AXIOM_MAX_MODEL_CALLS`, `AXIOM_TASK_TIMEOUT` (default 1800 s), `AXIOM_MAX_TOOL_ROUNDS`,
  `AXIOM_MAX_TOKENS`.
- `app/page.tsx`, `app/globals.css` — the dashboard. `components/` holds the rest of the UI.
- `AGENTS.md` — the collaboration contract, including the instruction that a new session
  reads `HANDOFF.md` first.

Two invariants the runtime depends on, both enforced by **tools rather than prompts**:
only Coder may write; Planner, Tester and Reviewer inspect and report. And static
parsing, real execution and visual checks are kept distinguishable in the API and in the
UI labels — never describe static-only output as runtime-tested.

## 4. Read before changing anything

- `DECISIONS.md` — ADR-001 … ADR-013, the "do not accidentally change" list. The three
  most load-bearing are ADR-001 (LangGraph *is* the runtime, not a hand-rolled loop),
  ADR-002 (the round budget counts rounds that change the project, not inspection) and
  ADR-011 (dependency manifests are allowed when the product needs them — do **not**
  "fix" a scope-drift report by banning manifests globally).
- `HANDOFF.md` — the session baton. **It is stale in one specific place:** its
  §"Current state" still says *no live run has exercised the new runtime yet*. That is no
  longer true; see §6. Everything else in it still holds.
- `README.md` — install, run, and the pipeline description.
- `AGENTS.md` — ownership rules and what counts as verification.

## 5. What this session added to the product

This session added the two newest commits. Everything from `795d9e1` back to `a56c14e`
came from the session before it and is listed for context, because the runtime behaviour
in §6 depends on that work. All of it is on `main` and pushed:

```text
49cb17c docs: ADR-012/013 om dashbord-tetthet og navigasjon (dashboard density + navigation)
05111db fix(web): gjor dashbordet kompakt og navigasjonen valgbar
795d9e1 docs: fill the handoff and record the architectural decisions
c6f4a7a fix: a continuation keeps the earlier model plan
9a754fd fix: stop an agent that repeats the same tool call forever
3096848 fix: stop cutting off long plans and reports
3d40534 fix: count work rounds, not inspection, against the agent round limit
ce09500 feat: continue a task from an earlier workspace
d37df1e fix: keep long runs alive and make subagent work attributable
a56c14e feat: LangGraph agent runtime with Coder subagents and a working key setup
```

The dashboard work at the end is worth one sentence of context because the numbers were
measured, not guessed: a single `.workflow-step` had grown to 3 989 px and the page to
**16 782 px**, because two text fields rendered raw agent output at full length — the
Planner's assignment is the entire task prompt, and the Reviewer's summary is a full
report. They are now clamped with a hover fallback; page height is **~2 080 px** and the
sidebar no longer scrolls sideways. The same session made the two nav items actually
select the view they name, and made past tasks clickable while a run is in flight.

## 6. Run history — the actual evidence

From `GET /api/tasks` on this machine, 2026-09-16 22:1x local. This is the raw material
for any workflow evaluation, and it is more interesting than a success story.

| id | task | status | calls | repair | how it ended |
|---|---|---|---|---|---|
| `0f7dacb9` | "Continue this work" | cancelled | 4 | 0 | cancelled by the user |
| `4e5f4e80` | **Zombiegata** (third-person WebGL shooter) | failed | 39 | 1 | Coder looped on `edit_file src/render/renderer.js`; the guard stopped it |
| `8f8cf23d` | Varg continuation | cancelled | 6 | 0 | cancelled by the user |
| `744af3f3` | **Varg i hagen** (garden game) | **completed / approved** | 38 | 0 | first full clean pass of the new runtime |
| `00ddb685` | Varg continuation | cancelled | 3 | 0 | cancelled by the user |
| `b0e06e49` | Varg continuation | cancelled | 7 | 0 | cancelled by the user |
| `1e7e25e6` | Varg i hagen (first attempt) | failed | 22 | 0 | OpenRouter HTTP 402, balance ran out |
| `f4dbba1f` | "Lag et random 3d spill" | failed | — | — | Coder exceeded its tool round limit (old ceiling) |
| `506d5380` | 3D kartspill | failed | — | — | Coder cut off at the 32 768-token output limit |
| `5906eb00` | sjakk | failed | — | — | Coder exceeded its tool round limit |
| `a5f3750d` | sjakk | failed | — | — | Coder returned 3 empty responses in a row |
| `99a51c48` | sjakk | failed | — | — | Coder returned an empty result |

Reading of that table, offered as interpretation rather than fact: the failures before
`a56c14e` are almost all *harness* failures — output limits, round ceilings that counted
inspection, empty responses, an exhausted balance. After the LangGraph rewrite the same
kind of task reaches the end and gets a verdict. The one failure since is qualitatively
different and more interesting: the Coder did not run out of room, it got stuck applying
the same edit repeatedly, which the repeat-guard caught (`9a754fd`).

Per-run cost is **not persisted** anywhere in `.axiom/tasks.sqlite3` — there is no cost
column. You have to read it in the OpenRouter dashboard. The previous session reported
roughly $2.22 for `744af3f3`; treat that number as unverified hearsay.

One capability in the runtime has still never been exercised: **Coder subagents have
never fired.** The `workers` array is empty for every task in the table above, including
the multi-file builds. The machinery exists (`backend/delegation.py`, ADR-010, plus tests
in `backend/tests/test_delegation.py`), so it is covered by unit tests but unproven in a
real run. `HANDOFF.md` lists "Coder uses at least one delegated subagent" as part of the
definition of done; that item is still open while "a task finishes with an approved
verdict" and "the repair edge fires" are both now satisfied.

Workspaces for these runs are in `github-local/.axiom/workspaces/<task-id>/` and the task
store is `github-local/.axiom/tasks.sqlite3`. **Old failed tasks and their workspaces are
evidence, not junk — do not delete them unasked** (this is also recorded in `HANDOFF.md`).

## 7. The benchmark games

Three small browser games exist as artefacts of a head-to-head comparison: the same brief
given to Axiom's pipeline and to a Codex session, then judged by the user playing both.

| Artefact | Path (machine-local) | Verified today |
|---|---|---|
| Axiom's Varg i hagen | `github-local/.axiom/workspaces/744af3f39aa64ec6a9b97574b752a233/` | `node tests/game.test.js` → **4 ok, 0 feil** (10 files) |
| My Varg i hagen | `.run/varg-codex/` | `node tests/game.test.js` → **4 ok, 0 feil** |
| Axiom's Zombiegata | `github-local/.axiom/workspaces/4e5f4e801a6840d0ae749a84d2a410d3/` | `node tests/game.test.js` → **10 ok, alle tester bestått** |
| My Zombiegata | `.run/zombie-codex/` | `node tests/game.test.js` → **15 ok, 0 feil** |

The result that matters for evaluation is not who "won". It is that **Axiom's reviewer
found a real defect I had missed** in its own output — that the third-person camera did
not follow the shot direction, so the crosshair lied about where bullets went — while the
Coder that wrote the file never noticed. Static review caught something static linting
never would. And in the other direction: **every defect the user actually felt was found
by the user playing the game**, in both implementations. Unit tests passed throughout.

There is also a prompt-design lesson here, worth carrying into any future eval. The brief
said "aim with the mouse (pointer lock)". Both implementations took that literally and
gated aiming *and firing* on `document.pointerLockElement === canvas`. In a browser that
refuses pointer lock, shooting silently did nothing — in two independently written games.
Naming a mechanism in a brief binds every implementation to it; describing the intended
behaviour instead would not have.

## 8. Verification status — what is proven, and what is not

Proven on this machine today:

- `python -m pytest backend/tests -q` → **67 passed** (one `anyio` deprecation warning).
- `npx tsc --noEmit` in `github-local/` → clean.
- The three game test suites listed above, run with `node`.
- The dashboard measurements in §5, taken with `getBoundingClientRect` in the live page.

Not proven, and the pipeline cannot prove it:

- **Axiom never executes or sees anything.** Tester and Reviewer work by reading code and
  running static validation. Every visual, gameplay and interaction claim in any run
  report is the writing model's own estimate. The runs say so themselves — that honesty
  is deliberate and should be preserved.
- The browser behaviour of the games: pointer lock, camera framing, whether shooting
  feels right. The only instrument that has ever caught these is a human at the screen.
- The Zombiegata repair is mid-flight: `4e5f4e80` ended `failed` with `repair_round: 1`
  and a pending blocking finding. The workspace is complete and its 10 tests pass, so it
  is a working artefact with one known control bug, not a wreck.

## 9. Open threads, in the order they were left

1. **Zombiegata needs one repair pass.** The blocking finding (camera does not follow the
   shot direction) plus the pointer-lock problem are both known and both located. A
   ready-to-paste repair prompt exists in `.run/zombie-brief.txt` and in the session
   handoff; it names `src/main.js` lines and instructs that firing must not depend on
   pointer lock. Nothing has been run on it yet.
2. **`HANDOFF.md` §"Current state" is stale** — one claim, described in §4 above. Worth
   fixing because `AGENTS.md` makes that file the entry point for every new session.
3. **`research/` is untracked.** See §1. The Aider-vs-LangGraph paper is the user's
   main written artefact outside the product and currently has no backup.
4. **Explicit task constraints are still not enforced.** A task that forbids dependencies
   or manifests is not checked against the product that comes out. This is a known,
   deliberately unfixed gap (ADR-011 explains why the obvious fix is wrong).
5. **The eval the user asked for has not been written.** The intent was a document about
   agent-workflow evaluation — the comparison between the pipeline and a Codex session —
   that the user could hand to another model. §6, §7 and §8 are the raw material for it.

## 10. Machine-local state (not reproducible elsewhere)

- Both servers were running during this session: backend on `127.0.0.1:8000`, dashboard on
  `127.0.0.1:3000`. The three games were being served on `8103`, `8104` and `8105`.
- `.run/` holds the prompts, logs and screenshots from the game runs
  (`varg-v3-prompt.txt`, `zombie-brief.txt`, `axiom-zombie.png`, `zombie-mine2.png`, …).
  It is scratch space; nothing in it is generated by the product.
- `test-temp/pytest-of-runef/` is pytest's own temporary output, including leftover
  sqlite files from test runs. Safe to ignore.
- The OpenRouter balance is a live dependency. One run died mid-build on HTTP 402. Check
  it before starting anything long.

## 11. Traps and dead ends — do not re-walk these

- **Do not fix a layout problem with a camera hack.** A previous round raised the game
  camera with a `PITCH_BIAS` constant to lift the character above the buttons. The real
  cause was `justify-content: space-between` on a flex HUD putting the control row in the
  middle of the screen. Fix the layout.
- **Do not ban dependency manifests globally** to stop scope drift (ADR-011).
- **Do not remove the text clamps** in the dashboard to make a panel look less cramped,
  and do not give a sidebar child a fixed pixel max-width — `max-width:190px` there was
  wider than the sidebar's own content box and produced a 13 826 px horizontal scroll
  (ADR-012).
- **Do not re-add a `disabled={running}` guard to the task list**, and do not hardcode the
  nav `active` class (ADR-013).
- **Do not simplify the runtime proxy's origin check back to string equality.** Accepting
  loopback origins was a real bug fix (`608a4f9`).
- **Do not treat a green test run as evidence that a product works.** In this project it
  has never once been true for the things the user could feel.

## 12. Plausible next steps

These are forks, not a sequence: the first two can proceed independently, the third is
the one that changes the pipeline's value.

1. **Finish Zombiegata** — run the prepared repair prompt against `4e5f4e80`, then verify
   in a browser that firing works without pointer lock and that the crosshair matches the
   shot. Low risk, closes an open loop.
2. **Write the workflow evaluation** the user wanted, using §6–§8: harness failures vs
   orchestration failures, reviewer-catches-what-coder-misses, human-in-the-loop as the
   only working verifier, and the two arms' incomparable cost bases (Axiom spends
   OpenRouter credit; the Codex arm spends tokens that are not measured anywhere).
3. **Give the pipeline a way to execute what it builds** — even a single `run` tool that
   executes the project's declared test command and records the output in the task. That
   is the one change that would have caught the defects this session actually cared about.

## 13. Support

Installed skills that fit this repo: `ce-handoff` (this document; resume from it),
`ce-debug` for the open Zombiegata bug, `ce-code-review` before merging pipeline changes,
`ce-plan`/`ce-work` for anything multi-step, and `ce-pov` if the eval turns into a
judgement call about the architecture.
