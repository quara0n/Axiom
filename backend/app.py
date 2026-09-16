from contextlib import asynccontextmanager
import ipaddress
import re

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from dotenv import set_key

from .config import Settings
from .provider import OpenRouter, ProviderError
from .runtime import Runtime
from .store import ROLES, Store, TERMINAL
from .workspace import Workspace, copy_workspace_files

MODEL_PATTERN = r"[A-Za-z0-9~][A-Za-z0-9_./:~-]{0,199}"

class TaskRequest(BaseModel):
    task: str = Field(min_length=1, max_length=20000)
    model: str | None = Field(default=None, max_length=200)
    models: dict[str, str] | None = None
    project_instructions: str = Field(default="", max_length=16000)
    continue_from: str | None = Field(default=None, max_length=64)


class ConnectionRequest(BaseModel):
    api_key: str = Field(max_length=512)


def create_app(settings=None, provider=None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        app.state.store = Store(settings.database)
        app.state.store.recover()
        app.state.provider = provider or OpenRouter(
            settings.api_key, max_tokens=settings.max_tokens,
            reasoning_max_tokens=settings.reasoning_max_tokens,
            request_timeout=settings.request_timeout,
        )
        app.state.api_key = settings.api_key
        app.state.runtime = Runtime(settings, app.state.store, app.state.provider)
        yield
        await app.state.runtime.close()
        await app.state.provider.close()

    app = FastAPI(title="Axiom local agent runtime", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.allowed_origins),
                       allow_methods=["GET", "POST"], allow_headers=["Content-Type"])
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
            if source["status"] not in TERMINAL:
                raise HTTPException(409, "Wait for that task to finish before continuing it.")
            # A continuation keeps the earlier team configuration unless the caller
            # overrides it; otherwise it silently falls back to the default model.
            if not body.model and not requested:
                model = source.get("model") or model
                per_agent = dict(source.get("models") or {})
        value = request.app.state.store.create(task, model, per_agent)
        # Project instructions carry over unless the new request overrides them,
        # so a follow-up keeps the rules the original task was built under.
        instructions = body.project_instructions.strip() or (source or {}).get("project_instructions", "")
        value["project_instructions"] = instructions
        value["workspace"] = str((settings.workspace_root / value["id"]).absolute())
        value["files"] = []
        if source:
            workspace = Workspace(settings.workspace_root / value["id"])
            try:
                seeded = copy_workspace_files(settings.workspace_root / source["id"], workspace)
            except (ValueError, OSError) as exc:
                raise HTTPException(422, workspace.error_message(exc)) from exc
            value["continue_from"] = source["id"]
            value["files"] = seeded
            value["continuation"] = {
                "task": source["task"], "status": source["status"], "summary": source["summary"],
                "reports": {agent["name"]: agent["result"] for agent in source["agents"] if agent["result"]},
                "verification": source.get("verification"),
                "note": (f"Continuing task {source['id']}. Its files are already in this workspace; "
                         "inspect them before changing anything and only redo work that is missing."),
            }
        request.app.state.store.save(value)
        runtime.start(value)
        return value

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

    return app


app = create_app()
