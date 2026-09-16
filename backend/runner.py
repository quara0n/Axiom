"""Discovery and bounded execution of the checks a project declares.

The model never supplies a command line: it names an intent, and this module
resolves that intent from what the workspace actually declares. Nothing here is an
operating-system sandbox — a child process runs with the same privileges as the
backend — which is why execution is opt-in and why every result says what it is.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

MAX_OUTPUT_CHARS = 4_000

# Names that could carry a credential into a generated project. The rest of the
# environment is passed through because package managers need it to work at all.
_CREDENTIAL = re.compile(r"api[_-]?key|token|secret|password|credential|auth", re.IGNORECASE)

PYTHON_TEST_MARKERS = ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini")


def execution_environment(environ=None):
    """The environment a declared check runs in: everything except credentials."""
    source = os.environ if environ is None else environ
    return {name: value for name, value in source.items()
            if not _CREDENTIAL.search(name) and not name.startswith("AXIOM_")}


def _read_json(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def declared_checks(workspace):
    """What this project says it can run. Reads files; runs nothing."""
    checks = []
    package_path = workspace.path("package.json")
    package = _read_json(package_path) if package_path.is_file() else None
    scripts = package.get("scripts") if isinstance(package, dict) else None
    scripts = scripts if isinstance(scripts, dict) else {}
    npm = shutil.which("npm")
    for intent, script in (("tests", "test"), ("build", "build")):
        if isinstance(scripts.get(script), str):
            checks.append({
                "intent": intent, "source": "package.json", "declares": scripts[script],
                "command": [npm, "test", "--silent"] if intent == "tests" else [npm, "run", "build"],
                "available": bool(npm),
                "reason": "" if npm else "npm is not on PATH for the backend process.",
            })
    python = shutil.which("python") or shutil.which("python3") or sys.executable
    python_project = any(workspace.path(marker).is_file() for marker in PYTHON_TEST_MARKERS)
    if not python_project:
        tests = workspace.path("tests")
        python_project = tests.is_dir() and bool(list(tests.glob("test_*.py")))
    if python_project and not any(check["intent"] == "tests" for check in checks):
        checks.append({"intent": "tests", "source": "python", "declares": "pytest",
                       "command": [python, "-m", "pytest", "-q"], "available": bool(python),
                       "reason": "" if python else "No Python interpreter is on PATH."})
    return checks


def resolve(workspace, intent):
    for check in declared_checks(workspace):
        if check["intent"] == intent:
            return check
    return None


def _tail(value):
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    return (value or "")[-MAX_OUTPUT_CHARS:]


def run_check(workspace, check, timeout):
    """Run one declared check as a bounded child process."""
    started = time.perf_counter()
    before = workspace_digest(workspace)
    try:
        completed = subprocess.run(check["command"], cwd=str(workspace.root),
                                   env=execution_environment(), capture_output=True, text=True,
                                   timeout=timeout, check=False, shell=False)
        exit_code, timed_out = completed.returncode, False
        output = _tail(completed.stdout) + _tail(completed.stderr)
    except subprocess.TimeoutExpired as exc:
        exit_code, timed_out = None, True
        output = _tail(exc.stdout) + _tail(exc.stderr)
    except (OSError, subprocess.SubprocessError) as exc:
        exit_code, timed_out = None, False
        output = f"{type(exc).__name__}: the declared check could not be started."
    return {
        "intent": check["intent"], "source": check["source"],
        "command": " ".join(str(part) for part in check["command"]),
        "exit_code": exit_code, "timed_out": timed_out,
        "duration_ms": round((time.perf_counter() - started) * 1000),
        "output": output.strip()[-MAX_OUTPUT_CHARS:],
        "changed_project": workspace_digest(workspace) != before,
        "isolated": False,
        "note": "Ran as a local process with no provider credentials in its environment. "
                "This is not an operating-system sandbox.",
    }


def workspace_digest(workspace):
    digest = hashlib.sha256()
    for name in workspace.files():
        digest.update(name.encode())
        digest.update(workspace.path(name).read_bytes())
    return digest.hexdigest()


def execution_outcome(workspace, settings):
    """The verification record's execution state, however it was reached."""
    checks = declared_checks(workspace)
    summary = [{"intent": check["intent"], "source": check["source"],
                "declares": check.get("declares", ""), "available": check["available"],
                "reason": check["reason"]} for check in checks]
    if not checks:
        return {"state": "not_run", "checks": [],
                "reason": "The project declares no test or build command to run."}
    if not getattr(settings, "allow_execution", False):
        return {"state": "not_run", "checks": summary,
                "reason": "Execution is disabled. Set AXIOM_ALLOW_EXECUTION=1 to run a "
                          "declared check."}
    chosen = next((check for check in checks if check["intent"] == "tests"), None)
    chosen = chosen or next((check for check in checks if check["intent"] == "build"), None)
    if not chosen["available"]:
        return {"state": "not_run", "checks": summary, "reason": chosen["reason"]}
    result = run_check(workspace, chosen, getattr(settings, "execution_timeout", 120))
    result["checks"] = summary
    result["state"] = "passed" if result["exit_code"] == 0 and not result["timed_out"] else "failed"
    return result
