from contextlib import asynccontextmanager
import ipaddress
import re

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import Settings
from .provider import OpenRouter, ProviderError
from .runtime import Runtime
from .store import Store, TERMINAL


class TaskRequest(BaseModel):
    task: str = Field(min_length=1, max_length=20000)
    model: str | None = Field(default=None, max_length=200)


def create_app(settings=None, provider=None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        app.state.store = Store(settings.database)
        app.state.store.recover()
        app.state.provider = provider or OpenRouter(settings.api_key)
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
    async def health():
        return {"configured": bool(settings.api_key), "model": settings.model,
                "workspace": str(settings.workspace_root), "status": "ok"}

    @app.get("/api/models")
    async def models(request: Request):
        try:
            return {"models": await request.app.state.provider.models()}
        except ProviderError as exc:
            raise HTTPException(502, str(exc)) from exc

    @app.post("/api/tasks", status_code=202)
    async def create_task(body: TaskRequest, request: Request):
        if not settings.api_key:
            raise HTTPException(503, "Set OPENROUTER_API_KEY in the backend environment before running tasks.")
        task = body.task.strip()
        if not task:
            raise HTTPException(422, "Task cannot be blank.")
        model = body.model or settings.model
        if not re.fullmatch(r"[A-Za-z0-9~][A-Za-z0-9_./:~-]{0,199}", model):
            raise HTTPException(422, "Invalid OpenRouter model ID.")
        runtime = request.app.state.runtime
        if len(runtime.jobs) >= 10:
            raise HTTPException(429, "Too many pending tasks. Wait or cancel a task.")
        value = request.app.state.store.create(task, model)
        value["workspace"] = str((settings.workspace_root / value["id"]).absolute())
        value["files"] = []
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
