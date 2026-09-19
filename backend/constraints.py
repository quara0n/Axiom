"""Explicit task file contracts, enforced at every agent mutation boundary.

These are product scope rules, not an OS sandbox. Natural language is deliberately
not converted with keyword matching: 'no dependencies' does not forbid a manifest
containing only a test script. The caller supplies exact writable/forbidden files.
"""

from pathlib import PureWindowsPath


def canonical(name):
    if not isinstance(name, str) or not name or len(name) > 240:
        raise ValueError("Constraint paths must be relative filenames of at most 240 characters.")
    normalized = name.replace("\\", "/")
    if (PureWindowsPath(name).drive or normalized.startswith("/") or
            any(part in {"", ".", ".."} or ":" in part or part.endswith((" ", "."))
                for part in normalized.split("/"))):
        raise ValueError("Constraint paths must be safe, exact relative filenames.")
    return normalized.casefold()


def normalize_constraints(value):
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - {"allowed_files", "forbidden_files"}:
        raise ValueError("Unknown task file constraint.")
    result = {}
    for field in ("allowed_files", "forbidden_files"):
        names = value.get(field)
        if names is None:
            continue
        if not isinstance(names, list) or len(names) > 200:
            raise ValueError("Each file constraint must list at most 200 exact filenames.")
        result[field] = sorted({canonical(name) for name in names})
    if set(result.get("allowed_files", [])) & set(result.get("forbidden_files", [])):
        raise ValueError("A file cannot be both allowed and forbidden.")
    return result


def check_write(constraints, path):
    name = canonical(path)
    allowed = constraints.get("allowed_files")
    if name in constraints.get("forbidden_files", []) or (allowed is not None and name not in allowed):
        raise ValueError("This file is outside the task's explicit file constraints.")


def check_files(constraints, files):
    failures = []
    for name in files:
        if name == "AGENTS.md":
            continue  # Runtime-owned guidance is not a generated product file.
        try:
            check_write(constraints, name)
        except ValueError:
            failures.append(name)
    return failures
