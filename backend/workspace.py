import ast
import json
import os
from pathlib import Path, PureWindowsPath
import stat

MAX_FILE_BYTES = 128_000
MAX_FILES = 200
MAX_TOTAL_BYTES = 4_000_000


class Workspace:
    """Confined file tools. Generated programs are never executed.

    Reject symlinks/reparse points at every existing path component. This is a
    local, single-user boundary, not an OS sandbox against concurrent hostile
    processes that can replace directories between checks and operations.
    """
    def __init__(self, root: Path):
        root = root.absolute()
        self._check_components(root)
        root.mkdir(parents=True, exist_ok=True)
        self.root = root.resolve()

    @staticmethod
    def _check_components(path):
        for part in [*reversed(path.parents), path]:
            if part.exists() or part.is_symlink():
                info = part.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise ValueError("Symbolic links and Windows reparse points are forbidden.")

    def path(self, name: str):
        if not isinstance(name, str) or not name or len(name) > 240:
            raise ValueError("A relative workspace path is required.")
        win = PureWindowsPath(name)
        parts = name.replace("\\", "/").split("/")
        if win.drive or name.startswith(("/", "\\")) or any(
            part in {"", ".", ".."} or ":" in part or part.endswith((" ", "."))
            or part.upper().split(".")[0] in {
                "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10))}
            or part.lower() in {".git", ".env", ".ssh", ".aws", ".codex"}
            for part in parts
        ):
            raise ValueError("Unsafe or protected workspace path.")
        candidate = self.root.joinpath(*parts)
        self._check_components(candidate)
        if not candidate.resolve().is_relative_to(self.root):
            raise ValueError("Path escapes the workspace.")
        return candidate

    def files(self):
        paths = []
        for base, directories, files in os.walk(self.root, followlinks=False):
            for name in [*directories, *files]:
                self._check_components(Path(base) / name)
            for name in files:
                paths.append((Path(base) / name).relative_to(self.root).as_posix())
                if len(paths) > MAX_FILES:
                    raise ValueError("Workspace file count limit exceeded.")
        return sorted(paths)

    def call(self, name: str, args: dict, role: str):
        if name == "list_files":
            return {"files": self.files()}
        if name == "read_file":
            target = self.path(args["path"])
            if target.stat().st_size > MAX_FILE_BYTES:
                raise ValueError("File exceeds read limit.")
            return {"path": args["path"], "content": target.read_text(encoding="utf-8")}
        if name == "write_file":
            if role != "Coder":
                raise ValueError("Only Coder can write project files.")
            target = self.path(args["path"])
            content = args["content"]
            if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_FILE_BYTES:
                raise ValueError("File exceeds write limit.")
            existing = self.files()
            if not target.exists() and len(existing) >= MAX_FILES:
                raise ValueError("Workspace file count limit reached.")
            total = sum((self.root / path).stat().st_size for path in existing)
            old_size = target.stat().st_size if target.exists() else 0
            if total - old_size + len(content.encode("utf-8")) > MAX_TOTAL_BYTES:
                raise ValueError("Workspace size limit reached.")
            target.parent.mkdir(parents=True, exist_ok=True)
            self._check_components(target)
            target.write_text(content, encoding="utf-8")
            return {"path": args["path"], "bytes": len(content.encode("utf-8"))}
        if name == "validate_file":
            target = self.path(args["path"])
            if target.stat().st_size > MAX_FILE_BYTES:
                raise ValueError("File exceeds validation limit.")
            content = target.read_text(encoding="utf-8")
            if target.suffix == ".py":
                ast.parse(content, filename=args["path"])
                check = "Python syntax only; code was not executed."
            elif target.suffix == ".json":
                json.loads(content)
                check = "JSON parsing only."
            else:
                raise ValueError("Safe validation currently supports .py and .json only.")
            return {"path": args["path"], "valid": True, "check": check}
        raise ValueError("Unknown tool.")


def tool_schema(name, description, properties=None, required=None):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties or {},
                       "required": required or [], "additionalProperties": False},
    }}


def tools_for(role):
    path = {"path": {"type": "string", "description": "Relative workspace file path"}}
    tools = [
        tool_schema("list_files", "List project workspace files."),
        tool_schema("read_file", "Read a UTF-8 workspace file.", path, ["path"]),
        tool_schema("validate_file", "Parse Python or JSON without executing code.", path, ["path"]),
    ]
    if role == "Coder":
        tools.append(tool_schema("write_file", "Create or replace a UTF-8 workspace file.",
                                 {**path, "content": {"type": "string"}}, ["path", "content"]))
    return tools
