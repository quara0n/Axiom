"""Starting a task that carries on from an earlier one.

Resume and continue are the same operation with different triggers, so they share
one implementation: the earlier workspace is copied in, its reports travel as
bounded context, and the new task names what it continues.
"""

from .coordination import handoff_text
from .store import now
from .workspace import Workspace, copy_workspace_files


def prepare_continuation(settings, store, task, model, per_agent, instructions, source, auto=None):
    """Create and publish a continuation task, or raise ValueError before publishing.

    Nothing is written to the store until the workspace copy has succeeded, so a
    rejected continuation cannot leave a queued task with no run behind it.
    """
    value = store.create(task, model, per_agent, persist=False)
    value["project_instructions"] = instructions
    value["workspace"] = str((settings.workspace_root / value["id"]).absolute())
    value["files"] = []
    workspace = Workspace(settings.workspace_root / value["id"])
    try:
        seeded = copy_workspace_files(settings.workspace_root / source["id"], workspace)
    except (ValueError, OSError) as exc:
        raise ValueError(workspace.error_message(exc)) from exc
    value["continue_from"] = source["id"]
    value["files"] = seeded
    value["continuation_depth"] = int(source.get("continuation_depth", 0)) + 1
    value["continuation"] = {
        "task": source["task"], "status": source["status"], "summary": source["summary"],
        # Reports carried into a new task are bounded for the same reason they are
        # bounded between roles: the full text stays in the earlier task's record.
        "reports": {agent["name"]: handoff_text(agent["result"])
                    for agent in source["agents"] if agent["result"]},
        "verification": source.get("verification"),
        "note": (f"Continuing task {source['id']}. Its files are already in this workspace; "
                 "inspect them before changing anything and only redo work that is missing."),
    }
    if auto:
        value["auto_recovery"] = {"reason": auto, "at": now()}
    store.save(value)
    return value
