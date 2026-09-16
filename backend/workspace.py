import ast
import difflib
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import stat
import subprocess
import tempfile

MAX_FILE_BYTES = 128_000
MAX_FILES = 200
MAX_TOTAL_BYTES = 4_000_000
# A range read is a budget, not a promise: the window is capped so one call cannot
# replay a whole generated file, and the caller is told what it did not get.
MAX_READ_LINES = 400
MAX_LINE_CHARS = 500
MAX_DIFF_CHARS = 4_000
MAX_OUTLINE_ENTRIES = 200

# Bumping this invalidates every recorded check, so a parser change is never hidden
# behind a reused result.
CHECK_VERSION = "static-parser-1"

# Only successful calls to these carry a result the agent can compare with the last
# one; a write or a failure is judged by its arguments alone.
READ_ONLY_TOOLS = {"list_files", "read_file", "search_files", "file_outline"}

_JS_PATTERNS = (
    (re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)"), "function"),
    (re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)"), "class"),
    (re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?"
                r"(?:function\b|\([^)]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)"), "function"),
    (re.compile(r"^\s*import\s+.+?from\s+['\"]([^'\"]+)['\"]"), "import"),
    (re.compile(r"^\s*export\s+\{([^}]*)\}"), "export"),
)
_HTML_PATTERNS = (
    (re.compile(r"<script[^>]*\bsrc\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE), "script"),
    (re.compile(r"<script\b(?![^>]*\bsrc\s*=)", re.IGNORECASE), "inline script"),
    (re.compile(r"<link[^>]*\bhref\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE), "link"),
    (re.compile(r"<(canvas|title|h1|h2|h3)\b[^>]*>", re.IGNORECASE), "element"),
)

# Files the Tester can check statically. Nothing here is executed: Python is parsed,
# JSON is decoded, JavaScript and inline HTML scripts are parsed by `node --check`.
CHECKABLE_SUFFIXES = (".py", ".json", ".js", ".mjs", ".cjs", ".html", ".htm")
_JS_SUFFIXES = {".js", ".mjs", ".cjs"}
_HTML_SUFFIXES = {".html", ".htm"}
_SCRIPT_TYPES = {"", "module", "text/javascript", "application/javascript", "text/ecmascript",
                 "application/ecmascript"}
_NODE_TIMEOUT = 30
_MAX_CHECK_OUTPUT = 600


def _node_environment():
    """Node needs a few system variables, but never the provider credentials."""
    names = ("PATH", "SystemRoot", "SYSTEMROOT", "windir", "TEMP", "TMP", "TMPDIR")
    return {name: os.environ[name] for name in names if name in os.environ}


def _node_check(source, suffix):
    """Parse JavaScript without running it. Returns (ok, message)."""
    node = shutil.which("node")
    if not node:
        raise ValueError("JavaScript validation requires Node.js on PATH.")
    order = (".mjs", ".cjs") if suffix == ".mjs" else (".cjs", ".mjs")
    message = "The JavaScript parser rejected this file."
    with tempfile.TemporaryDirectory() as folder:
        for candidate in order:
            target = Path(folder) / f"snippet{candidate}"
            target.write_text(source, encoding="utf-8")
            try:
                result = subprocess.run([node, "--check", str(target)], capture_output=True,
                                        text=True, timeout=_NODE_TIMEOUT, env=_node_environment(),
                                        check=False)
            except (OSError, subprocess.SubprocessError) as exc:
                raise ValueError(f"JavaScript validation could not run ({type(exc).__name__}).") from exc
            if result.returncode == 0:
                kind = "ES module" if candidate == ".mjs" else "CommonJS"
                return True, f"JavaScript parsed by node --check as {kind}; the code was not executed."
            message = (result.stderr or result.stdout or message).strip()
    return False, message[-_MAX_CHECK_OUTPUT:]


class _InlineScripts(HTMLParser):
    """Collect inline <script> bodies so they can be syntax checked."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self._type = None
        self._buffer = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self._type = (dict(attrs).get("type") or "").strip().lower()
            self._buffer = []

    def handle_endtag(self, tag):
        if tag == "script" and self._type is not None:
            self.blocks.append((self._type, "".join(self._buffer)))
            self._type = None

    def handle_data(self, data):
        if self._type is not None:
            self._buffer.append(data)


class Workspace:
    """Confined file tools. Generated programs are never executed.

    Validation parses source files; JavaScript and inline HTML scripts are read by
    `node --check`, which parses without running anything.

    Reject symlinks/reparse points at every existing path component. This is a
    local, single-user boundary, not an OS sandbox against concurrent hostile
    processes that can replace directories between checks and operations.
    """
    def __init__(self, root: Path, checks=None):
        root = root.absolute()
        self._check_components(root)
        root.mkdir(parents=True, exist_ok=True)
        self.root = root.resolve()
        # Recorded static checks, keyed by path. The caller owns the dict so the
        # record survives across the nodes that make up one task.
        self.checks = checks if isinstance(checks, dict) else {}

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

    def error_message(self, exc):
        """Return useful parser feedback while redacting local workspace/temp roots."""
        if isinstance(exc, SyntaxError):
            return f"Syntax error at line {exc.lineno}, column {exc.offset}: {exc.msg}"
        if isinstance(exc, OSError):
            return "File operation failed. Check that the relative path exists and is accessible."
        if isinstance(exc, (KeyError, TypeError)):
            return "Invalid tool arguments. Check the tool schema and required fields."
        message = str(exc)
        for path in (str(self.root), tempfile.gettempdir()):
            message = message.replace(path, "<workspace-or-temp>")
            message = message.replace(path.replace("\\", "/"), "<workspace-or-temp>")
        return message[:1500]

    def call(self, name: str, args: dict, role: str):
        if name == "list_files":
            return {"files": self.files()}
        if name == "read_file":
            target = self.path(args["path"])
            if target.stat().st_size > MAX_FILE_BYTES:
                raise ValueError("File exceeds read limit.")
            content = target.read_text(encoding="utf-8")
            first, last = args.get("start_line"), args.get("end_line")
            if first is None and last is None:
                return {"path": args["path"], "content": content}
            lines = content.splitlines()
            total = len(lines)
            first = self._line_number(first, 1, "start_line")
            last = self._line_number(last, total, "end_line")
            if first > total:
                raise ValueError(f"The file has {total} line(s); start_line is past the end.")
            if last < first:
                raise ValueError("end_line must not come before start_line.")
            clipped = min(last, first + MAX_READ_LINES - 1, total)
            body = "\n".join(f"{number}\t{lines[number - 1][:MAX_LINE_CHARS]}"
                             for number in range(first, clipped + 1))
            return {"path": args["path"], "content": body, "start_line": first,
                    "end_line": clipped, "total_lines": total,
                    "partial": first > 1 or clipped < total, "truncated": clipped < last,
                    "note": "Lines are numbered. Read the next range from end_line + 1."}
        if name == "file_outline":
            target = self.path(args["path"])
            if target.stat().st_size > MAX_FILE_BYTES:
                raise ValueError("File exceeds read limit.")
            content = target.read_text(encoding="utf-8")
            return self._outline(args["path"], target.suffix.lower(), content)
        if name == "search_files":
            query = args["query"]
            if not isinstance(query, str) or not query or len(query) > 500:
                raise ValueError("Search requires a nonempty literal query of at most 500 characters.")
            matches = []
            for filename in self.files():
                target = self.path(filename)
                if target.stat().st_size > MAX_FILE_BYTES:
                    continue
                try:
                    lines = target.read_text(encoding="utf-8").splitlines()
                except UnicodeError:
                    continue
                for number, line in enumerate(lines, 1):
                    if query in line:
                        matches.append({"path": filename, "line": number, "text": line[:500]})
                        if len(matches) >= 50:
                            return {"matches": matches, "truncated": True}
            return {"matches": matches, "truncated": False}
        if name == "edit_file":
            if role != "Coder":
                raise ValueError("Only Coder can edit project files.")
            target = self.path(args["path"])
            if target.stat().st_size > MAX_FILE_BYTES:
                raise ValueError("File exceeds edit limit.")
            old, new = args["old_text"], args["new_text"]
            if not isinstance(old, str) or not old or not isinstance(new, str):
                raise ValueError("old_text must be nonempty and new_text must be a string.")
            content = target.read_text(encoding="utf-8")
            if content.count(old) != 1:
                raise ValueError("old_text must match exactly once. Read the current file and include more context.")
            return self.call("write_file", {"path": args["path"], "content": content.replace(old, new, 1)}, role)
        if name == "write_file":
            if role != "Coder":
                raise ValueError("Only Coder can write project files.")
            target = self.path(args["path"])
            if target.name.lower() == "agents.md":
                raise ValueError("AGENTS.md is project guidance owned by the user and runtime, not the Coder.")
            content = args["content"]
            if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_FILE_BYTES:
                raise ValueError("File exceeds write limit.")
            previous = target.read_text(encoding="utf-8") if target.exists() else None
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
            # The writer gets its own change back, so it never has to re-read the file
            # to find out what it just did.
            return {"path": args["path"], "bytes": len(content.encode("utf-8")),
                    "lines": len(content.splitlines()), "created": previous is None,
                    "diff": "New file." if previous is None else self.diff(previous, content)}
        if name == "validate_file":
            target = self.path(args["path"])
            if target.stat().st_size > MAX_FILE_BYTES:
                raise ValueError("File exceeds validation limit.")
            content = target.read_text(encoding="utf-8")
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            recorded = self.checks.get(args["path"])
            if (isinstance(recorded, dict) and recorded.get("valid") is True
                    and recorded.get("sha256") == digest and recorded.get("version") == CHECK_VERSION):
                # Same bytes, same parser: the answer cannot differ, and saying it was
                # reused keeps the record honest.
                return {"path": args["path"], "valid": True, "cache": "reused",
                        "check": recorded.get("check"),
                        "note": "Unchanged since an earlier check in this task; not parsed again."}
            suffix = target.suffix.lower()
            if suffix == ".py":
                ast.parse(content, filename=args["path"])
                check = "Python syntax only; code was not executed."
            elif suffix == ".json":
                json.loads(content)
                check = "JSON parsing only."
            elif suffix in _JS_SUFFIXES:
                ok, message = _node_check(content, suffix)
                if not ok:
                    raise ValueError(f"JavaScript syntax error: {message}")
                check = message
            elif suffix in _HTML_SUFFIXES:
                scripts = _InlineScripts()
                scripts.feed(content)
                checked, failures = 0, []
                for script_type, body in scripts.blocks:
                    if script_type not in _SCRIPT_TYPES or not body.strip():
                        continue
                    checked += 1
                    ok, message = _node_check(body, ".mjs" if script_type == "module" else ".js")
                    if not ok:
                        failures.append(message)
                if failures:
                    raise ValueError(f"JavaScript syntax error in an inline script: {failures[0]}")
                note = f"{checked} inline script block(s) parsed" if checked else "no inline scripts found"
                check = ("Markup was handed to the standard HTML parser and " + note
                         + ". The markup itself is not validated and nothing was rendered or executed.")
            else:
                raise ValueError(
                    "Safe validation supports .py, .json, .js, .mjs, .cjs, .html and .htm.")
            self.checks[args["path"]] = {"sha256": digest, "version": CHECK_VERSION,
                                         "valid": True, "check": check}
            return {"path": args["path"], "valid": True, "check": check, "cache": "parsed"}
        raise ValueError("Unknown tool.")

    @staticmethod
    def _line_number(value, default, field):
        if value is None:
            return default
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"{field} must be a positive line number.")
        return value

    @staticmethod
    def diff(previous: str, current: str):
        """A bounded unified diff so a writer can see its own change."""
        lines = list(difflib.unified_diff(previous.splitlines(), current.splitlines(),
                                          lineterm="", n=1))[2:]
        text = "\n".join(line[:MAX_LINE_CHARS] for line in lines)
        if len(text) > MAX_DIFF_CHARS:
            return text[:MAX_DIFF_CHARS] + "\n... diff truncated; read the file for the rest."
        return text or "No textual change."

    def _outline(self, path: str, suffix: str, content: str):
        if suffix == ".py":
            return {"path": path, "kind": "parse", "outline": self._python_outline(content, path)}
        if suffix in _JS_SUFFIXES:
            return {"path": path, "kind": "scan", "outline": self._line_outline(content, _JS_PATTERNS),
                    "note": "Line scan, not a parse. Read the range you need before editing it."}
        if suffix in _HTML_SUFFIXES:
            return {"path": path, "kind": "scan", "outline": self._line_outline(content, _HTML_PATTERNS),
                    "note": "Line scan, not a parse. The markup itself is not validated."}
        if suffix == ".json":
            data = json.loads(content)
            keys = list(data) if isinstance(data, dict) else []
            return {"path": path, "kind": "parse",
                    "outline": "\n".join(keys[:MAX_OUTLINE_ENTRIES]) or "No top-level keys."}
        raise ValueError("An outline is available for .py, .js, .mjs, .cjs, .html, .htm and .json files.")

    @staticmethod
    def _python_outline(content, path):
        tree = ast.parse(content, filename=path)
        entries = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
                entries.append((node.lineno, kind, f"{node.name}({ast.unparse(node.args)})"))
            elif isinstance(node, ast.ClassDef):
                bases = ", ".join(ast.unparse(base) for base in node.bases)
                entries.append((node.lineno, "class", f"{node.name}({bases})" if bases else node.name))
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        entries.append((child.lineno, "  def", f"{child.name}({ast.unparse(child.args)})"))
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                module = f"{node.module}." if isinstance(node, ast.ImportFrom) and node.module else ""
                entries.append((node.lineno, "import", ", ".join(module + alias.name for alias in node.names)))
        body = "\n".join(f"{line}\t{kind} {name}" for line, kind, name in entries[:MAX_OUTLINE_ENTRIES])
        return body or "No top-level definitions."

    @staticmethod
    def _line_outline(content, patterns):
        entries = []
        for number, line in enumerate(content.splitlines(), 1):
            for pattern, kind in patterns:
                match = pattern.search(line)
                if match:
                    label = match.group(1).strip() if match.groups() else line.strip()[:80]
                    entries.append(f"{number}\t{kind} {label}")
                    break
            if len(entries) >= MAX_OUTLINE_ENTRIES:
                break
        return "\n".join(entries) or "Nothing recognisable in a line scan."


def tool_schema(name, description, properties=None, required=None):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties or {},
                       "required": required or [], "additionalProperties": False},
    }}


def copy_workspace_files(source: Path, target: "Workspace"):
    """Seed a new task from an earlier task's workspace.

    Guidance files are skipped: the runtime writes its own AGENTS.md so project
    instructions stay authoritative for the new task. The same file count, per-file
    and total size limits apply as for any other write, so an earlier task cannot
    push a workspace over the limits.
    """
    source = Path(source)
    if not source.is_dir():
        raise ValueError("There is no workspace to continue from.")
    copied, total = [], 0
    for base, directories, names in os.walk(source, followlinks=False):
        for name in [*directories, *names]:
            info = (Path(base) / name).lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("Symbolic links and Windows reparse points are forbidden.")
        for name in names:
            origin = Path(base) / name
            relative = origin.relative_to(source).as_posix()
            if Path(relative).name.lower() == "agents.md":
                continue
            content = origin.read_bytes()
            if len(content) > MAX_FILE_BYTES:
                raise ValueError(f"{relative} exceeds the workspace copy limit.")
            if copied and len(copied) >= MAX_FILES:
                raise ValueError("Workspace file count limit reached.")
            if total + len(content) > MAX_TOTAL_BYTES:
                raise ValueError("Workspace size limit reached.")
            destination = target.path(relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            copied.append(relative)
            total += len(content)
    return sorted(copied)


def tools_for(role):
    path = {"path": {"type": "string", "description": "Relative workspace file path"}}
    span = {"start_line": {"type": "integer", "description": "First line of the range, 1-based"},
            "end_line": {"type": "integer", "description": "Last line of the range, inclusive"}}
    tools = [
        tool_schema("list_files", "List project workspace files."),
        tool_schema("read_file",
                    "Read a UTF-8 workspace file, or one line range of it. A range answers with "
                    "numbered lines, the file's total line count and a partial-range notice, and is "
                    "cheaper than the whole file; read the whole file only when you need all of it.",
                    {**path, **span}, ["path"]),
        tool_schema("file_outline",
                    "List a file's imports and top-level definitions with line numbers, without "
                    "reading the body. Python and JSON are parsed; JavaScript and HTML are scanned.",
                    path, ["path"]),
        tool_schema("search_files", "Find literal text in workspace files; returns up to 50 matching lines.",
                    {"query": {"type": "string"}}, ["query"]),
        tool_schema("validate_file",
                    "Statically check a Python, JSON, JavaScript or HTML file without executing it. "
                    "A file already checked unchanged in this task is reported as reused.",
                    path, ["path"]),
    ]
    if role == "Coder":
        tools.append(tool_schema("write_file", "Create or replace a UTF-8 workspace file.",
                                 {**path, "content": {"type": "string"}}, ["path", "content"]))
        tools.append(tool_schema("edit_file", "Replace one exact occurrence in an existing file. Read it first; ambiguous matches are rejected.",
                                 {**path, "old_text": {"type": "string"}, "new_text": {"type": "string"}},
                                 ["path", "old_text", "new_text"]))
    return tools
