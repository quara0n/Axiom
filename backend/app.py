from contextlib import asynccontextmanager
import ipaddress
import re
import shutil

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from dotenv import set_key

from .config import Settings
from .continuation import prepare_continuation
from .preview import Preview
from .provider import OpenRouter, ProviderError
from .runtime import Runtime
from .store import ROLES, Store, TERMINAL

MODEL_PATTERN = r"[A-Za-z0-9~][A-Za-z0-9_./:~-]{0,199}"

# A task that was interrupted by a restart is still a valid thing to continue from;
# the workspace it wrote is on disk.
CONTINUABLE = TERMINAL | {"interrupted"}

class TaskRequest(BaseModel):
    task: str = Field(min_length=1, max_length=20000)
    model: str | None = Field(default=None, max_length=200)
    models: dict[str, str] | None = None
    project_instructions: str = Field(default="", max_length=16000)
    project: str | None = Field(default=None, max_length=60)
    continue_from: str | None = Field(default=None, max_length=64)


class ConnectionRequest(BaseModel):
    api_key: str = Field(max_length=512)


def create_app(settings=None, provider=None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        app.state.store = Store(settings.database)
        interrupted = app.state.store.recover()
        app.state.provider = provider or OpenRouter(
            settings.api_key, max_tokens=settings.max_tokens,
            reasoning_max_tokens=settings.reasoning_max_tokens,
            request_timeout=settings.request_timeout,
            max_attempts=settings.max_provider_attempts,
            retry_base=settings.provider_retry_base,
        )
        app.state.api_key = settings.api_key
        app.state.runtime = Runtime(settings, app.state.store, app.state.provider)
        app.state.preview = Preview(settings.preview_port)
        # Recovery spends the operator's money, so it happens only when asked for.
        if settings.auto_resume:
            for task_id in interrupted[: settings.max_auto_recovery]:
                source = app.state.store.get(task_id)
                if not source:
                    continue
                try:
                    value = prepare_continuation(
                        settings, app.state.store, source["task"],
                        source.get("model") or settings.model, dict(source.get("models") or {}),
                        source.get("project_instructions", ""), source,
                        auto="resumed automatically after a backend restart")
                except ValueError:
                    continue
                app.state.runtime.start(value)
        yield
        await app.state.runtime.close()
        await app.state.provider.close()
        app.state.preview.stop()

    app = FastAPI(title="Axiom local agent runtime", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.allowed_origins),
                       allow_methods=["GET", "POST", "DELETE"], allow_headers=["Content-Type"])
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = request.client.host if request.client else ""
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = host == "testclient"  # Starlette's in-process test transport.
        if not local:
            return JSONResponse({"detail": "Axiom backend accepts loopback clients only."}, status_code=403)
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if origin and origin not in settings.allowed_origins:
                return JSONResponse({"detail": "Origin is not allowed."}, status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "Cross-site mutations are forbidden."}, status_code=403)
        return await call_next(request)

    @app.get("/api/health")
    async def health(request: Request):
        return {"configured": bool(request.app.state.api_key), "model": settings.model,
                "workspace": str(settings.workspace_root), "status": "ok"}

    @app.post("/api/connection")
    async def connection(body: ConnectionRequest, request: Request):
        key = body.api_key.strip()
        if not key or any(ord(char) < 32 for char in key):
            raise HTTPException(422, "Enter a valid API key.")
        if request.app.state.runtime.jobs:
            raise HTTPException(409, "Wait for the active task before changing the API key.")
        provider = request.app.state.provider
        if not isinstance(provider, OpenRouter):
            raise HTTPException(501, "This provider cannot update its key.")
        try:
            # Write to the runtime's own state directory, never to .env: `next dev`
            # watches .env and would reload the dashboard mid-save.
            settings.credentials.parent.mkdir(parents=True, exist_ok=True)
            set_key(str(settings.credentials), "OPENROUTER_API_KEY", key)
        except OSError as exc:
            raise HTTPException(500, "Could not save the API key locally.") from exc
        await provider.configure_key(key)
        request.app.state.api_key = key
        return {"configured": True, "model": settings.model}

    @app.get("/api/models")
    async def models(request: Request):
        try:
            return {"models": await request.app.state.provider.models()}
        except ProviderError as exc:
            raise HTTPException(502, str(exc)) from exc

    @app.post("/api/tasks", status_code=202)
    async def create_task(body: TaskRequest, request: Request):
        if not request.app.state.api_key:
            raise HTTPException(503, "Add an OpenRouter API key in LLM settings before running tasks.")
        task = body.task.strip()
        if not task:
            raise HTTPException(422, "Task cannot be blank.")
        model = body.model or settings.model
        if not re.fullmatch(MODEL_PATTERN, model):
            raise HTTPException(422, "Invalid OpenRouter model ID.")
        requested = body.models or {}
        if any(role not in ROLES for role in requested):
            raise HTTPException(422, "Unknown agent role in the model plan.")
        per_agent = {}
        for role, value in requested.items():
            candidate = (value or "").strip()
            if not candidate:
                continue
            if len(candidate) > 200 or not re.fullmatch(MODEL_PATTERN, candidate):
                raise HTTPException(422, f"Invalid OpenRouter model ID for {role}.")
            per_agent[role] = candidate
        runtime = request.app.state.runtime
        if len(runtime.jobs) >= 10:
            raise HTTPException(429, "Too many pending tasks. Wait or cancel a task.")
        source = None
        if body.continue_from:
            source = request.app.state.store.get(body.continue_from)
            if not source:
                raise HTTPException(404, "The task to continue was not found.")
            if source["status"] not in CONTINUABLE:
                raise HTTPException(409, "Wait for that task to finish before continuing it.")
            # A continuation keeps the earlier team configuration unless the caller
            # overrides it; otherwise it silently falls back to the default model.
            if not body.model and not requested:
                model = source.get("model") or model
                per_agent = dict(source.get("models") or {})
        instructions = body.project_instructions.strip() or (source or {}).get("project_instructions", "")
        project = body.project or (source.get("project") if source else None)
        if source:
            try:
                value = prepare_continuation(settings, request.app.state.store, task, model,
                                             per_agent, instructions, source, project=project)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
        else:
            # Publish only after preparation succeeds; rejected requests must not leave
            # queued tasks with no corresponding runtime job.
            value = request.app.state.store.create(task, model, per_agent, persist=False,
                                                   project=project)
            value["project_instructions"] = instructions
            value["workspace"] = str((settings.workspace_root / value["id"]).absolute())
            value["files"] = []
            request.app.state.store.save(value)
        runtime.start(value)
        return value

    @app.post("/api/tasks/{task_id}/resume", status_code=202)
    async def resume(task_id: str, request: Request):
        """Continue an interrupted (or finished) task without retyping anything."""
        source = request.app.state.store.get(task_id)
        if not source:
            raise HTTPException(404, "Task not found.")
        if source["status"] not in CONTINUABLE:
            raise HTTPException(409, "Wait for that task to finish before resuming it.")
        return await create_task(TaskRequest(task=source["task"], continue_from=task_id), request)

    @app.get("/api/tasks")
    async def tasks(request: Request):
        return {"tasks": request.app.state.store.list()}

    @app.get("/api/tasks/{task_id}")
    async def task(task_id: str, request: Request):
        value = request.app.state.store.get(task_id)
        if not value:
            raise HTTPException(404, "Task not found.")
        return value

    @app.post("/api/tasks/{task_id}/cancel")
    async def cancel(task_id: str, request: Request):
        value = await task(task_id, request)
        if value["status"] not in TERMINAL:
            request.app.state.runtime.cancel(task_id)
            # Cancellation is asynchronous; status polling returns its durable result.
        return request.app.state.store.get(task_id)

    @app.delete("/api/tasks/{task_id}")
    async def delete_task(task_id: str, request: Request):
        """Delete a finished thread, and the workspace it wrote.

        A running task is refused: deleting the record while a job is still writing
        into its workspace would leave a live process with no owner.
        """
        value = request.app.state.store.get(task_id)
        if not value:
            raise HTTPException(404, "That thread was not found.")
        if value["status"] not in TERMINAL:
            raise HTTPException(409, "Cancel the task before deleting it.")
        request.app.state.store.delete(task_id)
        if re.fullmatch(r"[0-9a-f]{32}", task_id):
            shutil.rmtree(settings.workspace_root / task_id, ignore_errors=True)
        return {"deleted": task_id}

    @app.post("/api/tasks/{task_id}/preview")
    async def preview(task_id: str, request: Request):
        """Hand back the address of the page this run wrote.

        The page is served from this machine on its own port, so it is generated code
        running in the operator's browser — not a sandbox. Off unless the operator
        enables it.
        """
        value = await task(task_id, request)
        if not settings.allow_preview:
            raise HTTPException(409, "Preview is off. Set AXIOM_ALLOW_PREVIEW=1 and "
                                     "restart the backend to serve a run's page.")
        workspace = settings.workspace_root / value["id"]
        if not workspace.is_dir():
            raise HTTPException(404, "That run has no workspace to open.")
        pages = sorted(path.relative_to(workspace).as_posix()
                       for path in workspace.rglob("*.html"))
        if not pages:
            raise HTTPException(409, "That run wrote no HTML page to open.")
        entry = "index.html" if (workspace / "index.html").is_file() else pages[0]
        url = request.app.state.preview.select(workspace)
        return {"url": url + entry, "pages": pages[:20],
                "note": "Served from this machine on 127.0.0.1. This is generated "
                        "code running in your browser, not a sandbox."}

    return app


app = create_app()
