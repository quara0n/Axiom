# DECISIONS.md — Axiom architectural decisions

> Store durable architectural decisions here.
> This is not a session log. Only record decisions that future agents should continue to respect.

## How to use this file

Add an entry when a decision:
- affects multiple modules,
- defines an interface or responsibility,
- constrains future implementation,
- explains why an apparently simpler alternative was rejected.

Do not add routine implementation details.

---

## Decision template

### ADR-XXX — [Short decision title]

**Status:** Active  
**Date:** YYYY-MM-DD

**Context**

[What problem or constraint led to this decision?]

**Decision**

[What was decided?]

**Why**

[Why this approach?]

**Consequences**

- [Positive consequence]
- [Tradeoff / limitation]

**Do not accidentally change**

[What a future agent might be tempted to alter without understanding the intent.]

**Revisit when**

[Condition under which this decision should be reconsidered.]

---

## Active decisions

### ADR-001 — LangGraph is the agent runtime

**Status:** Active  
**Date:** 2026-09-16

**Context**

A sequential `for agent in agents` loop could not express repair, resumption or
parallel work. The first provider error ended a run, and a rejected review destroyed
the whole task because there was no edge back.

**Decision**

Orchestrate with a LangGraph `StateGraph`: one node per role, plus `validate`,
`finalize`, and `route_after_review` as the repair edge. Graph state is the
`WorkflowState` TypedDict in `backend/runtime.py`.

**Why**

Loops become explicit and bounded, failure text travels along an edge, and the graph
can later be checkpointed. LangGraph is a low-level orchestration *runtime* rather
than an agent abstraction, so prompts and tools stay in this repository.

**Consequences**

- New dependency (`langgraph>=1.0,<2`) and a graph mental model.
- Orchestration is no longer readable as one linear function; tests drive the graph.

**Do not accidentally change**

Do not reintroduce a hand-rolled sequential loop "because it looks simpler", and do
not move prompt construction into a framework abstraction.

**Revisit when**

Runs must survive a process restart (checkpointer) or a second orchestrator appears.

---

### ADR-002 — The round budget counts changes, not inspection

**Status:** Active  
**Date:** 2026-09-16

**Context**

A 13-file run made 49 tool calls and was cut off at the round ceiling with Tester and
Reviewer unused, because listing, reading and validating consumed the same budget as
writing.

**Decision**

`AXIOM_MAX_TOOL_ROUNDS` counts rounds that change the project (`write_file`,
`edit_file`, delegation). Inspection is bounded separately by `AXIOM_MAX_MODEL_CALLS`.

**Why**

The scarce resource is change, not observation. Starving inspection prevents an agent
from gathering the evidence it is required to have.

**Consequences**

- A runaway reader is now bounded by model calls instead.
- Two knobs must be reasoned about together.

**Do not accidentally change**

Do not count read/list/validate calls against the tool-round limit.

**Revisit when**

Tasks routinely exhaust model calls before they exhaust change rounds.

---

### ADR-003 — Repair is bounded, and approval cannot override validation

**Status:** Active  
**Date:** 2026-09-16

**Context**

A `changes_required` verdict used to fail the entire task, so the first reviewer
objection destroyed all completed work. Loosening that naively would let an agreeable
reviewer wave broken output through.

**Decision**

`AXIOM_MAX_REPAIR_ROUNDS` (default 2, ceiling 5) routes a rejection back to Coder with
the critique. A task still only completes when deterministic validation passes *and*
the verdict is `approved`.

**Why**

This turns a rejection into an iteration without letting a model's opinion overrule
facts.

**Consequences**

- More model calls per task.
- The loop must stay bounded or cost grows without limit.

**Do not accidentally change**

Never let an approval bypass `validation_errors`.

**Revisit when**

Most tasks reach the repair ceiling: then the prompts, not the bound, are wrong.

---

### ADR-004 — Credentials live outside `.env`

**Status:** Active  
**Date:** 2026-09-16

**Context**

Saving the API key from the dashboard rewrote `.env`. `next dev` watches that file,
reloaded the page mid-save, and the save appeared to fail.

**Decision**

The dashboard writes `.axiom/credentials.env` (`AXIOM_CREDENTIALS`). Precedence is:
process environment > `.env` > credentials file.

**Why**

It stops the dev server reloading on save, and lets an operator pin a key for one
session without editing tracked or watched files.

**Consequences**

- A key may exist in two places; precedence must be documented (it is).

**Do not accidentally change**

Do not write the dashboard key back into `.env`, and do not invert the precedence.

**Revisit when**

Key rotation or multiple accounts are needed.

---

### ADR-005 — Each agent can run its own model

**Status:** Active  
**Date:** 2026-09-16

**Context**

One model for all four roles forces a bad trade: inspection wants cheap and fast,
planning and review want strong.

**Decision**

A task carries `models: {Planner|Coder|Tester|Reviewer: model_id}`. `Runtime.model_for`
falls back to the task model. Unknown roles are rejected and ids must match a strict
pattern.

**Why**

Per-role choice is where quality and cost are actually traded.

**Consequences**

- Cost becomes a matrix rather than a number.
- A weak Reviewer is an expensive mistake, because it gates completion.

**Do not accidentally change**

Do not remove the fallback, and do not accept arbitrary role keys.

**Revisit when**

Role-specific defaults per task type are wanted.

---

### ADR-006 — Reasoning budget and output budget are separate knobs

**Status:** Active  
**Date:** 2026-09-16

**Context**

A reasoning model with an unbounded thinking budget spent 32 768 output tokens over
three minutes and emitted no tool call. The task failed having written nothing, which
reads as a lazy model rather than a configuration mistake.

**Decision**

`AXIOM_REASONING_MAX_TOKENS` (default 2048) caps thinking; `AXIOM_MAX_TOKENS`
(default 32768) caps the whole response. The reasoning field is dropped automatically
for providers that reject it. A response cut off by the output limit is retried once
with an instruction to reply with exactly one tool call, and only then fails, naming
the limit and the setting that changes it.

**Why**

Thinking and answering compete for one budget, and the resulting failure is invisible
without the provider's stop reason.

**Consequences**

- Long single-file writes may need a higher output budget.
- The corrective retry costs one extra model call.

**Do not accidentally change**

Do not treat an empty message as proof of a bad model without checking
`finish_reason`.

**Revisit when**

Providers expose a first-class, separately billed thinking budget.

---

### ADR-007 — Permissions are enforced by tools, not by prompts

**Status:** Active  
**Date:** 2026-09-16

**Context**

Prompts cannot stop a role from writing files, and generated project guidance is
model-authored text that flows into later prompts.

**Decision**

Only Coder receives write tools. Planner, Tester and Reviewer are read-only.
Generated `AGENTS.md` guidance must not grant filesystem or execution capability.

**Why**

A prompt is advice; a missing tool is a boundary.

**Consequences**

- Some legitimate cross-role edits must go through Coder or delegation.
- Tool schemas become security-relevant code and need tests.

**Do not accidentally change**

Do not hand write tools to inspecting roles for convenience.

**Revisit when**

A role genuinely needs a new capability: add it narrowly per role, never globally.

---

### ADR-008 — Static, executed and visual checks stay distinguishable

**Status:** Active  
**Date:** 2026-09-16

**Context**

Static parsing was reported in a way that could be read as "tested". A generated
chess game shipped with a misspelled title and a malformed FEN, and nothing in the
pipeline noticed, because nothing ever rendered it.

**Decision**

The API and the UI label each kind of check distinctly. Static validation never claims
execution, and a Reviewer verdict is never presented as runtime proof.

**Why**

"It parses" and "it runs" are the difference between a demo and a deliverable.

**Consequences**

- Results are more verbose and less impressive.
- Users can trust the labels.

**Do not accidentally change**

Do not relabel static checks as tests to make a summary look stronger.

**Revisit when**

The pipeline executes artifacts itself; then record the real outcome instead.

---

### ADR-009 — The dashboard proxy accepts loopback origins

**Status:** Active  
**Date:** 2026-09-16

**Context**

The proxy compared the browser's `Origin` header with `new URL(request.url).origin`.
Next normalises that URL to its own origin, so every request from
`http://127.0.0.1:3000` was rejected as cross-origin and saving the API key always
failed, while `localhost:3000` worked.

**Decision**

Compare against the `Host` header the client actually used, accept any loopback origin
(`localhost`, `127.0.0.1`, `::1`, any port), and reject remote origins plus opaque
origins unless the browser reports the request is same-site.

**Why**

The security boundary is loopback, not a particular string. String equality produced a
false negative without adding protection.

**Consequences**

- A page on another local port may call the proxy; the backend keeps its own stricter
  origin allow-list.

**Do not accidentally change**

Do not go back to exact origin equality, and do not drop the remote or opaque
rejection.

**Revisit when**

The dashboard is served on a non-loopback interface.

---

### ADR-010 — Coder subagents work in owned workspaces

**Status:** Active  
**Date:** 2026-09-16

**Context**

Parallel Coder work in one workspace would collide, and merged output would be
impossible to attribute to a worker.

**Decision**

Delegation creates isolated, owned workspaces with content digests and explicit caps
(`AXIOM_MAX_WORKERS=3`, `AXIOM_MAX_SUBAGENTS_PER_TASK=6`). Subagent work is
attributable in the task record.

**Why**

Parallel work needs a boundary and an owner, or it becomes an unmergeable mess.

**Consequences**

- More tokens per task.
- A merge and attribution step must exist for subagent output.

**Do not accidentally change**

Do not raise the caps without measuring merge conflicts and cost.

**Revisit when**

Subagents are used on most tasks, or a task genuinely needs more than six.

---

### ADR-011 — Dependency manifests are allowed when the product needs them

**Status:** Active  
**Date:** 2026-09-16

**Context**

The Coder role prompt says: "Include dependency manifests, build configuration and
launch instructions when needed to run the requested product." A handoff draft
proposed replacing that with a blanket ban on package manager files, build steps,
servers and manifests, on the evidence that run `f4dbba1f` added `package.json` and
`serve.py` to a request that specified a dependency-free page openable from
`index.html`.

**Decision**

Keep the permissive rule. A requested product may legitimately need a manifest, a build
step or a launch script to run at all; the Coder should provide what the product needs.
Constraints that forbid those things belong in the *task text*, and are the task
author's responsibility rather than a global role rule.

**Why**

A blanket ban would make the Coder unable to deliver anything that needs a build,
which is most real software. The observed problem was not that manifests are allowed —
it was that an explicit task constraint is not enforced anywhere, so a compliant-looking
run can still add files the task forbade.

**Consequences**

- The Coder may add files a task did not ask for; reviewers should judge that against
  the task, not against a global rule.
- Per-task constraints are advisory until something checks them.

**Do not accidentally change**

Do not add a global ban on manifests, build steps or servers to the Coder instructions,
and do not present the `f4dbba1f` evidence as proof that the permissive rule is wrong.

**Revisit when**

A task-constraint check exists (for example, a workspace rule derived from the task
text), or scope drift is observed on tasks that do *not* forbid the extra files.

---

### ADR-012 — The dashboard does not grow with the length of agent text

**Context**

Every text field in the control room holds model output, and some of it is enormous:
a Planner assignment is the whole task prompt, and a Reviewer summary is a full report.
Rendered at full length those fields made a single `.workflow-step` 3 989 px tall and
the page 16 782 px, so the task list the user needed was thousands of pixels away.

**Decision**

Clamp them. Long text fields inside the results panel are limited by CSS
(`-webkit-line-clamp` plus a capped, scrollable container) and the untruncated string
stays reachable through a `title` attribute. A panel's height is set by its layout,
never by how much text an agent happened to produce. The same rule applies to the
sidebar: a `nowrap` child must be allowed to shrink (`min-width:0`), or it will widen
its container instead of ellipsising.

**Why**

Scrolling is a symptom, not a style preference. The user could not reach the task list
while a run was loaded, which made every other part of the dashboard unusable. Truncating
with a hover fallback keeps the full text available without letting content length
decide the page height.

**Consequences**

- Summaries are shown as a preview; the full text is one hover (or one scroll inside
  the panel) away.
- Any future panel that renders raw agent text must adopt the same cap.
- Measured in the browser afterwards: page height 16 782 → ~2 080 px, and the sidebar
  no longer scrolls sideways.

**Do not accidentally change**

Do not "fix" a cramped-looking panel by removing the clamp, and do not give a text
field a fixed pixel width in the sidebar — `max-width:190px` there was wider than the
sidebar's own content box and produced a 13 826 px horizontal scroll.

**Revisit when**

The results panel gets a dedicated detail view, in which case the summary can be a
single line that opens the full report.

---

### ADR-013 — Navigation reflects the view the user is in

**Context**

The two nav items ("Control room", "Activity") were plain anchors, and the `active`
class was hardcoded on the first one. Clicking "Activity" scrolled to the section, but
the sidebar still highlighted "Control room", and because the task data loads
asynchronously the browser's own anchor jump landed at the wrong offset.

**Decision**

Nav clicks set view state and scroll the target section into view after the click,
`window.location.hash` is kept in sync so the view is linkable and survives a reload,
and the `active` class follows that state.

**Why**

"I cannot select the activity" was literally true: nothing in the sidebar changed when
it was clicked. A navigation control that does not show what it selected is broken even
if the scroll happens to work.

**Consequences**

- `#task` and `#activity` are both deep-linkable; the hash is read on mount.
- The recent-tasks list is always clickable, including while a run is in flight — the
  previous `disabled={running}` guard hid exactly the history you want during a run.

**Do not accidentally change**

Do not go back to a hardcoded `active` class, and do not re-add a `running` guard to
task selection.

**Revisit when**

There are more than two views; at that point this becomes a router rather than a pair
of anchors.
