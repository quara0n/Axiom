"""Bounded Coder delegation with isolated copies and explicit integration."""

import asyncio
import hashlib
from uuid import uuid4

from .provider import ProviderError
from .workspace import CHECKABLE_SUFFIXES, MAX_FILE_BYTES, MAX_FILES, MAX_TOTAL_BYTES, Workspace, tool_schema


def digest(content):
    return hashlib.sha256(content).hexdigest() if content is not None else None


class OwnedWorkspace(Workspace):
    def __init__(self, root, owned_files):
        super().__init__(root)
        self.owned_files = {self.path(name).relative_to(self.root).as_posix().casefold() for name in owned_files}

    def call(self, name, args, role):
        if name in {"write_file", "edit_file"}:
            path = self.path(args["path"]).relative_to(self.root).as_posix().casefold()
            if path not in self.owned_files:
                raise ValueError("This file is outside your assigned ownership. Report the needed change to Lead Coder.")
        return super().call(name, args, role)


def delegation_tools():
    worker_id = {"worker_id": {"type": "string"}}
    return [
        tool_schema("delegate_tasks",
                    "Run up to 3 independent Coder subagents concurrently in isolated workspace copies. Define interfaces first. Each needs exact, nonoverlapping writable filenames. Results are proposals, not integrated code.",
                    {"tasks": {"type": "array", "minItems": 1, "maxItems": 3, "items": {
                        "type": "object", "additionalProperties": False,
                        "properties": {"name": {"type": "string"}, "assignment": {"type": "string"},
                                       "acceptance_criteria": {"type": "string"},
                                       "owned_files": {"type": "array", "items": {"type": "string"},
                                                       "minItems": 1, "maxItems": 40}},
                        "required": ["name", "assignment", "acceptance_criteria", "owned_files"]}}}, ["tasks"]),
        tool_schema("inspect_worker", "Read a proposed changed file before integration. Inspect every changed file; use normal read_file for the current integrated version.",
                    {**worker_id, "path": {"type": "string"}}, ["worker_id", "path"]),
        tool_schema("integrate_worker", "Apply all inspected changes from a completed subagent. Refuses stale originals, invalid syntax, or workspace limits; does not overwrite conflicts.",
                    worker_id, ["worker_id"]),
    ]


DELEGATION_NAMES = {tool["function"]["name"] for tool in delegation_tools()}


class Delegation:
    def __init__(self, runtime, value, workspace):
        self.runtime, self.value, self.workspace = runtime, value, workspace
        self.candidates = {}

    async def call(self, name, args, previous):
        if name == "delegate_tasks":
            return await self.delegate(args["tasks"], previous)
        candidate = self.candidates.get(args.get("worker_id"))
        if not candidate:
            raise ValueError("Unknown subagent for this task.")
        if name == "inspect_worker":
            record, workspace = candidate["record"], candidate["workspace"]
            path = workspace.path(args["path"]).relative_to(workspace.root).as_posix()
            if path not in record["changed_files"]:
                raise ValueError("Choose a path from this subagent's changed_files.")
            result = workspace.call("read_file", {"path": path}, "Coder")
            # Digest the bytes that integration will copy: text-mode read/write rewrites
            # newlines, so a digest of the decoded text never matches on Windows.
            candidate["inspected"][path] = digest(workspace.path(path).read_bytes())
            return result
        if name == "integrate_worker":
            return await self.integrate(candidate)
        raise ValueError("Unknown delegation tool.")

    async def delegate(self, tasks, previous):
        limit = min(3, self.runtime.settings.max_workers)
        if not isinstance(tasks, list) or not 1 <= len(tasks) <= limit:
            raise ValueError(f"Delegate between 1 and {limit} tasks in one call.")
        existing = self.value.get("workers") or []
        if len(existing) + len(tasks) > self.runtime.settings.max_subagents_per_task:
            raise ValueError("This task has reached its total subagent budget. Continue directly as Lead Coder.")
        claimed, plans = set(), []
        for task in tasks:
            if not isinstance(task, dict):
                raise ValueError("Each subagent task must be an object.")
            for field, length in (("name", 80), ("assignment", 8000), ("acceptance_criteria", 4000)):
                if not isinstance(task.get(field), str) or not task[field].strip() or len(task[field]) > length:
                    raise ValueError(f"Subagent {field} is missing or too long (max {length}).")
            owned = task.get("owned_files")
            if not isinstance(owned, list) or not 1 <= len(owned) <= 40:
                raise ValueError("Each subagent needs 1-40 exact writable filenames.")
            paths = []
            for name in owned:
                path = self.workspace.path(name)
                relative = path.relative_to(self.workspace.root).as_posix()
                key = relative.casefold()
                if path.name.lower() == "agents.md" or path.is_dir():
                    raise ValueError("Subagent ownership must name project files, not guidance files or directories.")
                if any(key == other or key.startswith(other + "/") or other.startswith(key + "/") for other in claimed):
                    raise ValueError("Subagent file ownership overlaps. Split responsibilities before delegating.")
                claimed.add(key)
                paths.append(relative)
            plans.append({**task, "owned_files": paths})
        # Snapshot once before launching workers. No worker sees another's edits.
        snapshot = {}
        for path in self.workspace.files():
            content = self.workspace.path(path).read_bytes()
            if len(content) > MAX_FILE_BYTES:
                raise ValueError("A source file exceeds the workspace copy limit.")
            snapshot[path] = content
        if sum(map(len, snapshot.values())) > MAX_TOTAL_BYTES:
            raise ValueError("Source workspace exceeds the copy limit.")
        # Only a validated delegation creates the record list.
        records = self.value.setdefault("workers", [])
        candidates = []
        for plan in plans:
            worker_id = uuid4().hex
            root = self.runtime.settings.workspace_root / ".workers" / self.value["id"] / worker_id
            workspace = OwnedWorkspace(root, plan["owned_files"])
            for path, content in snapshot.items():
                target = workspace.path(path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            record = {"id": worker_id, **plan, "status": "pending", "report": "",
                      "changed_files": [], "model": self.runtime.model_for(self.value, "Coder"),
                      "repair_round": self.value.get("repair_round", 0)}
            candidate = {"record": record, "workspace": workspace,
                         "baseline": {path.casefold(): digest(content) for path, content in snapshot.items()},
                         "inspected": {}}
            records.append(record)
            self.candidates[worker_id] = candidate
            candidates.append(candidate)
        self.runtime.event(self.value, "Coder", f"Delegated {len(candidates)} independent work package(s).")
        outcomes = await asyncio.gather(*(self.run_worker(candidate, previous) for candidate in candidates),
                                        return_exceptions=True)
        for outcome in outcomes:
            # A cancelled task or an exhausted task-wide budget is not a proposal failure.
            if isinstance(outcome, BaseException):
                raise outcome
        return {"workers": [candidate["record"] for candidate in candidates],
                "next_step": "Inspect every changed file and integrate useful completed proposals. Failed proposals are not applied."}

    async def run_worker(self, candidate, previous):
        record, workspace = candidate["record"], candidate["workspace"]
        label = "Coder/" + record["name"]
        try:
            async with self.runtime.worker_semaphore:
                record["status"] = "running"
                self.runtime.event(self.value, label, "Subagent started in an isolated workspace copy.")
                record["report"] = await self.runtime.run_agent(
                    self.value, "Coder", workspace, previous, worker=record)
                for path in record["owned_files"]:
                    target = workspace.path(path)
                    if target.is_file() and digest(target.read_bytes()) != candidate["baseline"].get(path.casefold()):
                        record["changed_files"].append(path)
                record["status"] = "completed" if record["changed_files"] else "failed"
                if not record["changed_files"]:
                    record["report"] += " No project changes were produced."
                self.runtime.event(self.value, label, f"Proposal {record['status']}: {len(record['changed_files'])} changed file(s).")
        except asyncio.CancelledError:
            record["status"] = "cancelled"
            self.runtime.event(self.value, label, "Subagent cancelled.")
            raise
        except ProviderError:
            # The task-wide model-call budget stops the whole task. A subagent's own
            # tool-round limit only fails that proposal, so its siblings still count.
            if self.value.get("model_calls", 0) >= self.runtime.settings.max_model_calls:
                raise
            record["status"] = "failed"
            record["report"] = "Subagent stopped at its tool round limit. Inspect events and either continue directly or delegate a smaller task."
            self.runtime.event(self.value, label, record["report"])
        except Exception:
            # Isolate a worker failure; retain successful sibling proposals.
            record["status"] = "failed"
            record["report"] = "Subagent failed or exhausted its budget. Inspect events and continue directly or delegate a smaller task."
            self.runtime.event(self.value, label, record["report"])

    async def integrate(self, candidate):
        record, source = candidate["record"], candidate["workspace"]
        if record["status"] == "integrated":
            return {"applied": [], "already_integrated": True}
        if record["status"] != "completed":
            raise ValueError("Only completed subagent proposals can be integrated.")
        changes = {}
        for path in record["changed_files"]:
            content = source.path(path).read_bytes()
            if candidate["inspected"].get(path) != digest(content):
                raise ValueError(f"Inspect the current proposed content of {path} before integration.")
            if path.lower().endswith(CHECKABLE_SUFFIXES):
                await asyncio.to_thread(source.call, "validate_file", {"path": path}, "Tester")
            changes[path] = content
        # Check all paths and limits before applying any file. There are no awaits
        # between this preflight and commit, so another task coroutine cannot race it.
        current = {path: self.workspace.path(path).read_bytes() for path in self.workspace.files()}
        before = {}
        for path in changes:
            target = self.workspace.path(path)
            before[path] = target.read_bytes() if target.exists() else None
            if digest(before[path]) != candidate["baseline"].get(path.casefold()):
                raise ValueError(f"Integration conflict in {path}: the original changed after delegation. Inspect and resolve it as Lead Coder.")
        # Case-insensitive keys also enforce quotas correctly on Windows.
        projected = {path.casefold(): content for path, content in current.items()}
        projected.update({path.casefold(): content for path, content in changes.items()})
        if (len(projected) > MAX_FILES or sum(map(len, projected.values())) > MAX_TOTAL_BYTES
                or any(len(content) > MAX_FILE_BYTES for content in changes.values())):
            raise ValueError("Integration would exceed workspace file or size limits.")
        applied = []
        try:
            for path, content in changes.items():
                target = self.workspace.path(path)
                target.parent.mkdir(parents=True, exist_ok=True)
                applied.append(path)
                target.write_bytes(content)
        except OSError:
            for path in reversed(applied):
                target = self.workspace.path(path)
                if before[path] is None:
                    target.unlink(missing_ok=True)
                else:
                    target.write_bytes(before[path])
            raise
        record["status"] = "integrated"
        self.value["files"] = self.workspace.files()
        self.runtime.event(self.value, "Coder/" + record["name"], f"Lead Coder integrated {len(applied)} inspected file(s).")
        return {"applied": applied, "verification": "Changed supported files passed static parsing; integration and runtime behavior still need review."}
