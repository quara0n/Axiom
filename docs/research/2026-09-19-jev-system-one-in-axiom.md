# JEV (TypeSafe System One) in Axiom — deep dive

Researched 2026-09-19. Every claim below is either **verified** (fetched from a primary
source or run against a live endpoint today) or marked **vendor-reported**. Sources are
listed at the end. Nothing in the runtime was changed by this review, no dependencies
were installed and no paid model calls were made.

Scope note: the word "typograf" was dropped on request. It matches only the unrelated
Russian typography library `typograf/typograf`, and appears nowhere in the JEV ecosystem.

---

## 1. Verdict in one paragraph

JEV is not a cheaper LLM. It is a **decision primitive** — you send a `state` and typed
`questions`, and get back choices, rubric scores and yes/no probabilities instead of
generated text. Axiom's measured cost is not dominated by "thinking"; it is dominated by
**replayed context, whole-file reads and a fixed four-role LLM round**
(`docs/research/2026-09-16-token-harness-analysis.md`, sections A–H). JEV attacks exactly
those: score which context to drop, rank which files and sections to read, detect a
stagnating loop, and pre-judge review questions — at roughly **$0.0004 per 10k-token
call**, which is two to three orders of magnitude below the LLM rounds it can prevent.

The blocker is access, not architecture: Axiom has no TypeSafe key, and JEV is **not**
available on OpenRouter (verified today, see §8). There is a working prototype path with
no key at all — SimpleJev's public demo API (§6) — and a drop-in adapter that lets the
JEV-shaped code run against our existing OpenRouter key (§7).

---

## 2. What JEV is (verified)

| Item | Detail |
| --- | --- |
| Vendor | TypeSafe AI, first "System One" model, early access from 2026-09-15 |
| Endpoint | `POST https://api.typesafe.ai/v1/systemone`, `Authorization: Bearer $TYPESAFE_API_KEY` |
| Request | `{state, model, questions{‹id›: {type, instructions, criteria}}}` |
| `state` | string, JSON object, or array of text values |
| `choice` | `criteria` = map of option → description (or `null`); returns one option + full distribution |
| `score` | `criteria` = ordered array of levels, lowest first; returns probability-weighted value |
| `noul` | yes/no; returns a single probability 0–1 |
| Answer fields | `choice`/`score`/`noul`, plus `probabilities` and `confidence` for Choice and Score |
| Usage | `usage.input_tokens`, `usage.output_tokens` |
| Model | `jev-1.13.0`, alias `jev-latest` (and `jev-preview`, currently the same) |
| Price | $0.042 per 1M input tokens; output tokens are free |
| Limits | 64k tokens/request (32k for `state` + the longest question); 250k tok/s and 1,200 req/min, **adjusting dynamically** |
| Modalities | Text only. No image, audio or video input |
| Language | English is the primary training language and where accuracy is best |
| Data | TypeSafe states customer requests/responses are not used for training |

Two design facts that matter for us:

1. **Question ids are for code.** They are not sent to the model and play no part in
   inference. All semantics must live in `instructions` and `criteria`.
2. **Independent questions in one request run in parallel against the same state.**
   Asking twenty questions costs one request, not twenty round trips. Speculation is
   cheap; code filters out the answers that do not apply.

What JEV is not: not a chatbot, code generator or explanation engine; it cannot return an
arbitrary string; typed output guarantees the interface, **not the truth**; and
`confidence` measures how concentrated the distribution is, not whether it is safe to act
on the answer.

---

## 3. Mapping onto Axiom's measured cost drivers

From `2026-09-16-token-harness-analysis.md`. The middle column is the JEV move; the right
column is where it would live.

| Driver in our code | JEV move | Where |
| --- | --- | --- |
| **B.** `read_file` returns the whole file (22 KB `render3d.js` read four times) | Rank candidate files and line ranges against the question before reading; read only the winners | `backend/workspace.py`, runtime tool loop |
| **C.** History grows and is replayed every round (cumulative ~`n·B + g·n²/2`) | Typed relevance scoring per tool result: keep, shrink or drop; replaces the 200k-character heuristic | `backend/coordination.py::compact_messages` |
| **D.** Whole role reports are passed to the next report reader | Score report sections against the *next* role's question instead of forwarding the report whole | `backend/runtime.py::run_agent` |
| **E.** Several roles repeat the same syntax check | Judge "has this file version already been checked, and did it pass" and reuse the recorded result | `validate` node, ledger |
| **F.** Errors surface late because we never run the product | Pre-screen artifacts for the failure classes static parsing cannot see (dead code, unused handlers, missing wiring) | validation node |
| **G.** A fixed four-agent round is not always the cheapest flow | Route task shape → which roles are needed; skip the Reviewer LLM round when typed checks already decide | `backend/runtime.py` graph |
| **H.** Reasoning and model choice need measurement per role | Choose model and reasoning depth per turn from task shape and difficulty | per-role model settings |
| **Known issue 1.** Explicit task constraints are not enforced (a dependency-free request got `package.json` + `serve.py`) | Score the diff against the task's stated constraints as `noul` questions | review node |

---

## 4. Ranked opportunities

Ordered by expected saving per unit of work and by how cheap it is to be wrong.

**1. Stagnation and loop detection — highest value, lowest risk.**
`4e5f4e80` died on a Coder repeat-loop, not on a limit. Today the runtime nudges with
fixed strings (empty response, no evidence, truncation, malformed verdict). A small
`score` or `choice` over the last *k* actions ("is the agent making progress toward the
task, repeating itself, or drifting beyond scope?") turns a wasted repair round into an
early stop. Precedent: `ProgressGate`, `fast-jev-compaction`.

**2. Typed context compaction — the largest raw token lever.**
Cumulative input grows quadratically when everything is replayed. JEV scores each tool
result and each turn for continued relevance; code drops only inspection output, never
file state or the task. Precedent: `fast-jev-compaction`.

**3. Retrieval: rank before reading.**
Our analysis already recommends deterministic `read_file(path, start, end)`,
`file_outline`, `read_symbol` and a repo map. JEV adds the semantic ranking stage on top:
one request scores N candidate ranges against the question. Precedent: `jev.nvim`,
`Every`, `blink`, `jev-tree` (for candidate sets above the 255-option cap).

**4. Reviewer pre-gate as typed questions — the biggest single round saved.**
The Reviewer is a full LLM round with its own tool calls and a ~2 KB report. A large share
of its checks are narrow and semantic: does the diff respect the task's constraints, does
the report's "verified" claim match the validation record, is the stated file list
complete. Those are `noul`/`score` questions. Keep the LLM Reviewer for code comprehension
and keep deterministic validation as the hard gate. Precedent: `Foreman`, `jev-review`,
`jev-belay`.

**5. Report → role handoff scoring.** Related to 2 and 4: score report sections for
relevance to the receiving role rather than forwarding the whole report (driver D).

**6. Per-turn routing.** Pick model and thinking depth from a typed read of the task and
the last actions (driver H). Precedent: `jev-router`, `jev-codex-router`.

**7. Tool-call guardrails.** A hazard score in front of mutating tools and MCP calls
(`allow_execution` / `allow_mcp` are real boundaries), plus prompt-injection screening of
fetched text. Precedent: `jev-axi`, `@riskaverse/toolgate`, `jev-mcp`.

**8. Claim verification against evidence.** Check what a role *says* it did against the
actual diff and validation record. Precedent: the citation-check cookbook, `jev-belay`.

**9. Task cache and continuation pick.** `backend/continuation.py` already decides whether
a new task continues an old one; JEV can make that judgement semantic rather than textual.

**10. Bench grading.** `backend/bench.py` compares arms (`roles`, `single`, `void`).
JEV can grade artifacts independently and cheaply, and estimate task difficulty for
routing. Precedent: `jev-eval-agent`, `Jev DSPy Lab`.

Where JEV must **not** go: writing code, long explanations, and anything visual. Visual
checks need a vision model — our own handoff records a case where static review approved a
defect the screen would have shown.

---

## 5. Where the code goes

Follows the existing shape of the repo; no second implementation of an existing
responsibility.

- **`backend/jev.py` (new).** A `JevClient` next to `OpenRouter` in `provider.py`, with
  the same style: `httpx.AsyncClient` with `base_url="https://api.typesafe.ai/v1"`,
  bearer auth, retry on 429/5xx honouring `retry-after`, bounded backoff, a dedicated
  `JevError`, and usage/`model` captured from the response. A hand-rolled client avoids
  the official SDK's new dependencies (`httpx2`, `pydantic`, `tenacity`) and matches how
  OpenRouter is already integrated; the official `typesafe-sdk` 0.7.0 stays an option.
- **`backend/config.py`.** `typesafe_api_key`, `jev_model` (default the pinned
  `jev-1.13.0`, not the moving alias), `jev_base_url`, `jev_request_timeout`,
  `jev_enabled`, and one threshold per gate. Key precedence must stay exactly as it is for
  OpenRouter: process environment > `.env` > `.axiom/credentials.env`.
- **Usage ledger (plan U1).** Record `purpose` (not a role), the model the response
  reports, input/output tokens, latency, the probability distribution and the branch code
  took. Distributions are what make thresholds tunable later.
- **Boundary rule.** JEV never writes a file, never grants a tool, and never turns a
  deterministic validation failure into an approval. Gates are harness-side; if agents get
  a tool at all it is one narrow read-only `ask_jev`.
- **Tests.** Fake transport, fixtures for all three primitives, and unit tests for each
  threshold policy. No test may require a key or the network.

---

## 6. Cost math

A 10k-token state costs **$0.00042**. A Reviewer round on a mid-tier model costs roughly
**$0.03**. So about **70 JEV calls equal one LLM reviewer round**. Even if JEV is wrong
often enough that a human or LLM must arbitrate 5 % of the time, the arithmetic still
favours it — provided the gates are placed where being wrong is cheap and reversible.

Optimise for **cost and time per verified good delivery**, not tokens per agent. A gate
that prevents one repair round pays for thousands of calls.

---

## 7. SimpleJev, and the open reproductions (the "is it open source by now?" question)

**Yes — SimpleJev is open source: `featherless-ai/simple-jev`** (219 stars). It turns any
compatible Hugging Face model into a classifier/JEV endpoint by reading next-token logits
for each question; the model never generates the JSON, the server builds it from the
scores.

- Endpoints: `POST /v1/classifier`, with **`/v1/systemone` as an alias** — the same
  request shape as TypeSafe, so our client can point at either.
- Public demo API, **no login or key**, 2k-token context, 2 requests/second.
- Local server with plain Transformers/PyTorch: `Qwen/Qwen3.5-0.8B` on CPU or
  `google/gemma-4-26B-A4B-it` on a GPU. Ships a `common/` folder of shared validation,
  prompt and scoring rules.
- Differences from TypeSafe JEV: Choice 2–50 options (vs 255), Noul limited to 0.01–0.99,
  Score 2–50 levels, English only, no calibration claim, text only. It explicitly says it
  does not reproduce TypeSafe's architecture, training, accuracy or calibration.

**How we should use it:** as the development and CI stand-in for the JEV path. We can
build and test every gate in Axiom today, for free and with no account, then point the
same client at TypeSafe when a key exists. That removes the access blocker entirely from
Phase 0 and 1.

Wider open ecosystem, if we ever want a self-hosted decision model rather than a hosted
one: `TheoLeeCJ/SemIf` + `openjev` (1,815★, home 3090), `ekzhang/openjev-sglang`
(Jev-compatible, prefill-only), `razorback16/openjev`, `logan-markewich/jeff`,
`zhengxuyu/litjev`, `OmniJev/PlayJev`, `vinnylarouge/jevlike`, and on Hugging Face
`com-kotobalabs/open-jev-deberta-v3-large`, `Meanblock/JEV-CPU` and
`Heman10x-NGU/openJev-verdict-2.0`.

---

## 8. Access paths, and the OpenRouter question

| Path | Needs | Note |
| --- | --- | --- |
| TypeSafe direct (`/v1/systemone`) | `TYPESAFE_API_KEY` | Best calibration; early-access account |
| Vercel AI Gateway (`typesafe-ai/jev`) | gateway key | Speaks the AI SDK `evaluate()` shape, not OpenAI chat |
| Cloudflare Workers AI (`typesafe/jev`) | Cloudflare account | Edge deployment path |
| System One adapter (Python) | any LLM key | **Drop-in client backed by OpenAI/Anthropic-style APIs** — lets the JEV interface run on our existing OpenRouter key while we wait |
| SimpleJev demo / local | nothing | Free development and offline tests |
| **OpenRouter** | — | **Not available.** Today's public model list has 0 ids matching `jev` or `typesafe` |

**OpenRouter connection — verified live today.** Axiom already has a working OpenRouter
provider (`backend/provider.py`: base URL `https://openrouter.ai/api/v1`, key from
`OPENROUTER_API_KEY`). Calling `GET /api/v1/key` with the key already stored in
`github-local/.env` returned: valid, `is_free_tier: false`, `usage: 16.004` USD,
`limit: null`. So the API connection itself works and needs no code change.

What is missing is **measurement, not connectivity**: `OpenRouter.complete` already
returns `usage`, `resolved_model`, `generation_id`, `attempts` and `latency_ms`, and
nothing persists them (analysis driver A). Adding JEV without the ledger would mean
guessing at the savings it produces.

Nothing named `TYPESAFE_*` exists in `.env` or `.axiom/credentials.env` today, so JEV
itself needs one of the key paths above.

---

## 9. Risks and unknowns

1. **Non-English state.** Our prompts, reports and several tasks are Norwegian; JEV is
   English-primary. Thresholds must be fitted on our own Norwegian states, not on the
   vendor's examples.
2. **Vendor limits move.** Rate limits are documented as dynamically adjusting during
   early access; treat 250k tok/s and 1,200 req/min as today's number.
3. **Version drift.** An alias can move under us. Pin `jev-1.13.0`, log the model the
   response reports, and re-check thresholds when we move.
4. **32k state budget.** Reports plus diffs can exceed it; long states must be chunked or
   selected rather than truncated silently.
5. **Calibration is aggregate.** Confidence describes a group of predictions, not this
   answer. High-impact gates keep a human or LLM fallback.
6. **Typed output ≠ truth.** A JEV verdict may never override deterministic validation.
7. **New dependencies.** The official Python SDK pulls `httpx2`, `pydantic` and
   `tenacity`; a hand-rolled client fits the existing provider style and adds nothing.

---

## 10. Proposed phases

**Phase 0 — plumbing, no behaviour change.** `JevClient`, config, ledger fields, offline
tests with a fake transport; prototype every gate against SimpleJev's free demo. Done
when: tests pass without a key, and one real `choice`/`score`/`noul` call is recorded in
the ledger from either SimpleJev or TypeSafe.

**Phase 1 — the two cheapest wins.** Stagnation/loop detection and typed context
compaction. Measure by replaying recorded tasks and comparing cumulative input tokens and
model calls against the current runtime, with no new failures.

**Phase 2 — review and handoff.** Reviewer pre-gate, report-section scoring, repair
routing. Measure cost and wall time per approved delivery and the number of repair rounds.

**Phase 3 — routing, guardrails, grading.** Per-turn model selection, tool-call hazard
scores, bench grading, and a calibration lock file that fits thresholds on our own
labelled cases and fails CI when a JEV update breaks them.

Each phase is additive: nothing in the deterministic validation path is replaced, and a
JEV outage must degrade to today's behaviour, not to a failed task.

---

## Sources

Fetched or run 2026-09-19 unless noted.

- TypeSafe docs — introduction, primitives, confidence, patterns:
  <https://docs.typesafe.ai/introduction>, `/primitives`, `/confidence`, `/patterns`
- TypeSafe HTTP API reference: <https://docs.typesafe.ai/api>
- TypeSafe models, aliases, price and limits: <https://docs.typesafe.ai/models>
- Official agent skill (`typesafe-ai/skills`, SKILL.md): <https://github.com/typesafe-ai/skills>
- Official Python SDK 0.7.0: <https://pypi.org/project/typesafe-sdk/>
- Vercel AI SDK TypeSafe provider: <https://ai-sdk.dev/providers/ai-sdk-providers/typesafe-ai>
- SimpleJev: <https://github.com/featherless-ai/simple-jev> (demo at
  <https://simple-jev-demo-api.featherless.ai/v1/>)
- Ecosystem indexes: <https://github.com/AnotiaWang/awesome-jev>,
  <https://github.com/Anil-matcha/awesome-jev-by-typesafe>
- Live checks: `GET https://openrouter.ai/api/v1/key` (key valid, $16.004 used) and
  `GET https://openrouter.ai/api/v1/models` (0 ids matching `jev` or `typesafe`)
- Internal: `docs/research/2026-09-16-token-harness-analysis.md`,
  `docs/plans/2026-09-16-2304-feat-axiom-harness-efficiency-plan.md`, `HANDOFF.md`,
  `AGENTS.md`
