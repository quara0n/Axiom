# Axiom agent dashboard

## Python agent runtime

Install and run the backend from the project root (Python 3.11+):

```sh
python -m pip install -e ".[test]"
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
python -m pytest backend/tests -q
```

See `.env.example` for OpenRouter and runtime settings.

A key entered in the dashboard's LLM settings is stored in `.axiom/credentials.env`
rather than `.env`. `next dev` watches `.env*` and reloads the page whenever one
changes, which interrupted the save and left the dialog hanging on "Saving
connection…". A key set in the real environment or in `.env` still takes precedence.

`backend/runtime.py` uses a compiled LangGraph `StateGraph`:

```text
START -> Planner -> Coder -> static checks -> Tester -> Reviewer -> decision
                      ^                                             |
                      +------------ repair findings ----------------+
                                                                    |
                                                                   END
```

Planner owns architecture, module interfaces, acceptance criteria and milestones.
Coder is the sole code writer and integrates the product. Tester and Reviewer
inspect the result independently of Coder; they do not write competing versions.
Static validation runs before their reports, so failures are available as evidence.
Reviewer rejection or parser failures return the findings to Coder, followed by
fresh validation, testing and review. The plan is retained instead of regenerated.

`AXIOM_MAX_REPAIR_ROUNDS` defaults to 2 additional passes (0 disables repair).
Unchanged files after an unsuccessful repair stop the loop early.
`AXIOM_MAX_MODEL_CALLS` caps calls across the entire task, including repairs.
`AXIOM_TASK_TIMEOUT` defaults to 1800 seconds for the whole task, subagents and
repair rounds included; a timed-out task keeps the files it already wrote.
`AXIOM_MAX_TOOL_ROUNDS` counts only rounds that change the project — writes, edits
and delegation. Reading and validating files does not count, because inspecting an
inherited codebase is diligence rather than a runaway loop; inspection is bounded by
`AXIOM_MAX_MODEL_CALLS`.
The API records `round_history`, `repair_round`, `model_calls`, and `verification`.
Each invocation has separate task state and prior reports. Agents retain their
individual model selection and bounded workspace tool loops.
These checks parse source; they do not execute generated applications.
Timeouts, cancellation, concurrency limits and SQLite task/event snapshots remain
managed by the local runtime. No LangGraph server or LangSmith account is required.

### Coder subagents

For larger tasks the Lead Coder can call `delegate_tasks` to run up to three
independent work packages in isolated workspace copies, each with explicit file
ownership that `write_file` and `edit_file` enforce. A subagent cannot delegate
further and cannot write outside its owned files. Its result is a proposal, not
integrated code: the Lead Coder must inspect every changed file and then integrate
it. Integration re-parses supported files and refuses a proposal whose original
changed in the meantime.

Subagent calls share the task's model-call budget. `AXIOM_MAX_WORKERS` bounds how
many subagents run at once and how many fit in one delegation call;
`AXIOM_MAX_SUBAGENTS_PER_TASK` bounds the total per task. The isolated copies live
under `<workspace root>/.workers/<task>` and are deleted when the task ends; the
durable per-subagent reports stay in SQLite.

### Continuing an earlier task

Every task starts in its own workspace, so a follow-up inherits nothing by default.
Pass `continue_from` (a task id) to the task API, or press "Continue from this task"
in the dashboard, to seed the new task from the earlier one: project files are copied
in (guidance files are not — the runtime writes its own `AGENTS.md`), the earlier
reports and verification are handed to every agent, and project instructions carry
over unless the new request overrides them. The copy obeys the same file count,
per-file size and symlink limits as any other write. Agents are told that the code
already exists and to rebuild only what is missing.

This is not a resume of the old run. There is no graph checkpointing: the new task is
a fresh pass over the inherited files, with its own budget, repair rounds and
verification. It exists so interrupted or finished work can be continued instead of
rebuilt from nothing.

### Shared project guidance

New workspaces receive an `AGENTS.md` with common collaboration rules. Optional
project instructions entered in the dashboard (or the `project_instructions` task
API field) are appended. The runtime reads and snapshots this file before running
agents and includes the same instructions in every handoff. An existing root
AGENTS.md is preserved. Coder cannot rewrite guidance to relax its own constraints.
Only the task workspace's root AGENTS.md is loaded; repository-root and nested
AGENTS.md files are not automatically inherited by generated projects.

The default guidance covers playable vertical slices, game controls, camera,
visual direction and performance considerations for interactive projects. It is
guidance, not evidence that those requirements have been implemented or tested.
`search_files` locates literal text, and `edit_file` replaces one exact occurrence
to avoid rewriting large files for small repairs. Old tool payloads are compacted
when context grows; agents can re-read files when needed.

### Measuring the harness

`approved` from the Reviewer is a claim made inside the run, so it cannot be the
number the harness is judged by. `backend/bench.py` supplies the missing half:
tasks with a grader the harness never sees, a negative control that must fail, a
golden solution that must pass, and one headline metric - cost and wall-clock per
solved task.

It runs the same task through two arms: the four-role pipeline and a single-loop
Pi-class baseline that shares the workspace boundary, the usage ledger and the
budgets but carries one context and a short instruction block. The difference
between them is therefore about how many contexts the harness needs, not about
which tools it has. See `bench/README.md` for the task format, the arm contract
and the commands.

The checkers grade behaviour they can execute, never how a result looks or feels;
visual and design judgement stays in the operator's rubric beside the report and
is never folded into the solve rate.

### Remaining capabilities

### Tool servers (MCP)

The Coder can reach a Model Context Protocol server, which is how Blender or another
outside tool becomes available to an agent. `backend/mcp.py` speaks stdio JSON-RPC:
it starts the configured command once, asks for its tool list, and routes calls to it
with a timeout and a truncated reply. Tools arrive prefixed with the server name, so
`blender__make_cube` cannot collide with `read_file`.

Two things are deliberate. A server is spawned only when the operator has turned MCP
on and an agent actually reaches for one of its tools, because a server is someone
else's code running with this backend's privileges. And only the lead Coder sees those
tools: a subagent working in an isolated copy has no business driving Blender.

```sh
# .env
AXIOM_ALLOW_MCP=1
AXIOM_MCP_SERVERS=[{"name":"blender","command":"uvx","args":["blender-mcp"]}]
```

`GET /api/mcp` reports what is configured without starting anything, and
`POST /api/mcp/probe` starts the servers and lists the tools they offer.

For ambitious 3D games, the next major capability is an isolated execution worker
that can install dependencies and build the project, plus a browser worker that
can inspect screenshots, exercise controls and capture console errors. Those
workers need explicit evidence tied to the code revision being reviewed. Neither
worker is implemented here: the dashboard labels runtime and visual behavior as
unverified even when static review passes. A Maintenance role would be a separate,
targeted workflow rather than an extra mandatory hop for every feature.

There is no checkpoint resume or human approval pause. After a backend restart,
unfinished tasks are marked failed and must be submitted again. Generated files
and SQLite reports remain on disk.

---

# vinext-starter

A clean full-stack starter running on [vinext](https://github.com/cloudflare/vinext), with optional Cloudflare D1 and Drizzle support.

## Prerequisites

- Node.js `>=22.13.0`
- Portable: Windows, macOS, or Linux; no Bash required
- Managed Linux: managed Linux runtime with Bash, `flock`, `curl`, `sha256sum`, and GNU `timeout`
- Git is required only for publishing

## Sites Lifecycle

The Sites initializer copies the shared starter and selects managed-linux only when `SITES_MANAGED_LINUX_CONTAINER=1`; otherwise it selects portable. It saves the selection only in ignored `.sites-runtime/execution-profile.json`. Both profiles copy/configure first, then use the plugin's separate `install-dependencies.mjs` step to measure installation independently. Edit source under `app/` and follow the Sites skill for installation, preview, builds, and publishing.

Whenever reopening or moving a checkout, run `node <plugin-root>/scripts/configure-execution-profile.mjs` before project commands. Profile changes do not alter tracked source or require reinstalling otherwise-valid dependencies; restart an existing preview to use the new selection. Do not commit or upload `.sites-runtime/`.

This starter does not use `wrangler.jsonc`.

`install:ci` runs `npm ci` once against the shared lockfile, disables parent-workspace discovery, and includes required dev/optional dependencies despite production/omit settings. Sharp defaults to prebuilt binaries unless explicitly configured otherwise. Do not overlap installers.

- **Portable:** Preserve host HOME, npm cache, registry, proxy, temporary paths, retry/concurrency settings, and lifecycle-script policy. Use `--prefer-offline --no-audit --no-fund`.
- **Managed Linux:** Use the existing project-local HOME/cache/tmp setup and Linux install lock, tarball preflight, and timeout. Restore the image-seeded npm cache only when its lockfile hash matches; retain network fallback. Builds keep their existing timeout. These helpers are not invoked by the portable profile.

`scripts/sites-env.mjs` preserves the caller's HOME, npm cache, proxy, XDG, and temporary-directory configuration while defaulting Wrangler and Miniflare state to the checkout. If npm reports an unwritable cache, select a writable path with `npm_config_cache` for that install. The `dev` and `start` scripts also keep Wrangler logs inside the checkout. Generated `.sites-runtime/` and `.wrangler/` directories are disposable and ignored by Git.

On portable, `npm run dev` uses `vinext dev` with HMR, starting at port 5173. Vinext records the running server in ignored `.vinext/` state, rejects an ordinary duplicate launch, and recovers stale state after a stopped process; exactly simultaneous starts can race. Pass `--port <port>` or `--hostname <host>` after `npm run dev --` when needed; keep portable previews on loopback.

On managed Linux, use `sites-preview start` only for requested browser QA. The project's dev script runs Vite and accepts the supervisor's `--host 0.0.0.0 --port 4173 --strictPort` arguments. The internal browser uses `http://terminal.local:4173/`; it is not a user-facing URL. The supervisor owns the preview lifecycle. The ignored local profile survives the supervisor's cleared process environment.

The portable profile simulates ChatGPT sign-in only for loopback development requests. Visit `/signin-with-chatgpt?return_to=/` to sign in as `local_seedy` (`seedy@sites.test`, display name `Seedy`) and `/signout-with-chatgpt?return_to=/` to sign out. The development cookie preserves that identity across server restarts. Mock auth is disabled in the managed-linux profile and is not included in production builds; hosted authentication remains dispatch-owned.

The Worker uses `vinext/server/fetch-handler`, including Vinext's config-aware image handling. After building, `npm start` runs that Worker locally through Wrangler on `127.0.0.1`, sharing `.wrangler/state` with dev preview and local D1 migrations; it does not deploy the site or simulate sign-in. Use the URL printed by the server. Pass `npm start -- --port <port>` to select a different built-preview port.

Local previews use Miniflare's placeholder `Request.cf` metadata without a network lookup. Set `CLOUDFLARE_CF_FETCH_ENABLED=true` to opt into fetching preview metadata; this setting does not change hosted request metadata.

Local tool usage metrics are disabled by default. Set `WRANGLER_SEND_METRICS=true` to opt in.

## Included Shape

- edit site code under `app/`
- `app/chatgpt-auth.ts` provides optional dispatch-owned ChatGPT sign-in helpers
- `.openai/hosting.json` declares optional Sites D1 and R2 bindings
- `vite.config.ts` simulates declared bindings for local development
- `db/index.ts` reads the D1 binding from the Cloudflare Worker environment
- `db/schema.ts` starts intentionally empty
- `@cloudflare/workers-types` provides Worker types; `cloudflare-env.d.ts` declares optional `DB`/`BUCKET` bindings—update these declarations if binding names change
- `examples/d1/` contains an optional D1 example surface
- `drizzle.config.ts` supports local migration generation when needed

## Workspace Auth Headers

Signed-in visitors receive both `oai-authenticated-user-id` and `oai-authenticated-user-email`. Private Sites require every visitor to sign in; public Sites may also have anonymous visitors, for whom neither header is present.

The user ID is stable for the same user on the same Site and different across Sites. Use it as the durable user key; use email and name for display or contact purposes.

SIWC-authenticated workspace sites may also receive `oai-authenticated-user-full-name` when the user's SIWC profile has a non-empty `name` claim. The full-name value is percent-encoded UTF-8 and is accompanied by `oai-authenticated-user-full-name-encoding: percent-encoded-utf-8`.

Treat the full name as optional and fall back to email when it is absent:

```tsx
import { headers } from "next/headers";

export default async function Home() {
  const requestHeaders = await headers();
  const userId = requestHeaders.get("oai-authenticated-user-id");
  const email = requestHeaders.get("oai-authenticated-user-email");
  const encodedFullName = requestHeaders.get("oai-authenticated-user-full-name");
  const fullName =
    encodedFullName &&
    requestHeaders.get("oai-authenticated-user-full-name-encoding") ===
      "percent-encoded-utf-8"
      ? decodeURIComponent(encodedFullName)
      : null;

  const displayName = fullName ?? email;
  // ...
}
```

## Optional Dispatch-Owned ChatGPT Sign-In

Import the ready-to-use helpers from `app/chatgpt-auth.ts` when the site needs optional or required ChatGPT sign-in:

- Use `getChatGPTUser()` for optional signed-in UI.
- Use the returned `userId` as the stable user key for user-owned records; do not use email as a durable identifier.
- Use `requireChatGPTUser(returnTo)` for server-rendered pages that should send anonymous visitors through Sign in with ChatGPT.
- In a Server Component, start sign-in with `<a href={chatGPTSignInPath(returnTo)} target="_top">`. The auth helper module is server-only; do not import it into a Client Component.
- Do not use `fetch`, XHR, a client-side router, or a framework link that can prefetch the sign-in route. SIWC must start as a top-level navigation.
- Never request the AuthAPI authorization endpoint directly. The dispatch-owned `/signin-with-chatgpt` route must start the SIWC flow.
- Use `chatGPTSignOutPath(returnTo)` for browser sign-out links or actions.
- Pass a same-origin relative `returnTo` path for the destination after sign-in or sign-out. The helper validates and safely encodes it.
- Mark protected pages with `export const dynamic = "force-dynamic"` because they depend on per-request identity headers.

Dispatch owns `/signin-with-chatgpt`, `/signout-with-chatgpt`, `/callback`, the OAuth cookies, and identity header injection. Do not implement app routes for those reserved paths. Routes that do not import and call the helper remain anonymous-compatible.

SIWC establishes identity only; it does not prove workspace membership. Use the Sites hosting platform's access policy controls for workspace-wide restrictions, or enforce explicit server-side membership or allowlist checks.

Use SIWC for account pages, user-specific dashboards, saved records, and write actions tied to the current ChatGPT user. Leave public content anonymous.

## Local D1 migrations

For a D1-backed local preview, generate SQL with `npm run db:generate`. Build once through the Sites skill's build entrypoint (or `npm run build` for standalone use) to generate `dist/server/wrangler.json`, rebuilding if bindings change. From the project root, apply each pending migration in order:

```sh
node --import ./scripts/sites-env.mjs ./node_modules/wrangler/bin/wrangler.js d1 execute DB --local --config dist/server/wrangler.json --persist-to .wrangler/state --file drizzle/0000_example.sql
```

Replace the filename with the pending migration and `DB` with your D1 binding name if different. Use `.wrangler/state`, not `.wrangler/state/v3`; Wrangler adds the versioned directories. Do not replay migrations already applied locally. This updates only the preview database; publishing applies production migrations separately.

## Diagnostic Commands

- `npm run install:ci`: perform the one locked dependency install
- `npm run dev`: start the Vite/Vinext development server
- `npm run build`: build the deployable Sites artifact
- `npm run start`: preview the built Worker locally with D1/R2 support
- `npm run db:generate`: generate Drizzle migrations after schema changes

When using the Sites plugin, follow its skill instructions for installation, builds, and publishing. These npm commands remain available for standalone use.

The portable build runs Vinext directly without a host `timeout` command. The managed-linux build uses `scripts/build-verified.sh` and its existing `SITES_BUILD_TIMEOUT` setting.

## Learn More

- [vinext Documentation](https://github.com/cloudflare/vinext)
- [Drizzle D1 Guide](https://orm.drizzle.team/docs/get-started/d1-new)
