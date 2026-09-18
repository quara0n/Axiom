from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    api_key: str = ""
    model: str = "openrouter/free"
    workspace_root: Path = Path(".axiom/workspaces")
    database: Path = Path(".axiom/tasks.sqlite3")
    credentials: Path = Path(".axiom/credentials.env")
    task_timeout: float = 1800
    max_tool_rounds: int = 60
    max_repair_rounds: int = 2
    max_model_calls: int = 240
    max_workers: int = 3
    max_subagents_per_task: int = 6
    max_tokens: int = 32768
    reasoning_max_tokens: int = 2048
    request_timeout: float = 180
    # Running generated code is a real boundary decision, so it is off until the
    # operator turns it on. Discovery of declared checks is always on.
    allow_execution: bool = False
    execution_timeout: float = 120
    # Serving the page a run wrote is the same kind of decision as running it.
    allow_preview: bool = False
    preview_port: int = 8100
    # An MCP server is another process that offers tools (Blender, for one). Starting
    # one runs someone else's code with this backend's privileges, so it is opt-in.
    allow_mcp: bool = False
    mcp_servers: str = ""
    mcp_timeout: float = 120
    # A transient provider failure should not end a task, and recovery that spends
    # money on its own stays off until the operator asks for it.
    max_provider_attempts: int = 3
    provider_retry_base: float = 1.0
    auto_resume: bool = False
    auto_continue: bool = False
    max_auto_recovery: int = 1
    allowed_origins: tuple[str, ...] = (
        "http://localhost:3000", "http://127.0.0.1:3000",
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:8000", "http://127.0.0.1:8000",
    )

    @classmethod
    def from_env(cls):
        # A key set in the real environment wins. The dashboard writes the key to the
        # credentials file instead of .env, because `next dev` reloads the whole page
        # whenever a watched .env file changes.
        process_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        load_dotenv()
        credentials = Path(os.getenv("AXIOM_CREDENTIALS", ".axiom/credentials.env")).absolute()
        if not process_key and credentials.is_file():
            load_dotenv(credentials, override=True)
        if process_key:
            os.environ["OPENROUTER_API_KEY"] = process_key
        return cls(
            api_key=os.getenv("OPENROUTER_API_KEY", "").strip(),
            model=os.getenv("AXIOM_MODEL", "openrouter/free"),
            workspace_root=Path(os.getenv("AXIOM_WORKSPACE_ROOT", ".axiom/workspaces")).absolute(),
            database=Path(os.getenv("AXIOM_DATABASE", ".axiom/tasks.sqlite3")).absolute(),
            credentials=credentials,
            task_timeout=max(30, min(float(os.getenv("AXIOM_TASK_TIMEOUT", "1800")), 7200)),
            max_tool_rounds=max(1, min(int(os.getenv("AXIOM_MAX_TOOL_ROUNDS", "60")), 200)),
            max_repair_rounds=max(0, min(int(os.getenv("AXIOM_MAX_REPAIR_ROUNDS", "2")), 5)),
            max_model_calls=max(1, min(int(os.getenv("AXIOM_MAX_MODEL_CALLS", "240")), 1000)),
            max_workers=max(1, min(int(os.getenv("AXIOM_MAX_WORKERS", "3")), 3)),
            max_subagents_per_task=max(1, min(int(os.getenv("AXIOM_MAX_SUBAGENTS_PER_TASK", "6")), 12)),
            max_tokens=max(1024, min(int(os.getenv("AXIOM_MAX_TOKENS", "32768")), 200000)),
            reasoning_max_tokens=max(0, min(int(os.getenv("AXIOM_REASONING_MAX_TOKENS", "2048")), 100000)),
            request_timeout=max(30, min(float(os.getenv("AXIOM_REQUEST_TIMEOUT", "180")), 1800)),
            allow_execution=os.getenv("AXIOM_ALLOW_EXECUTION", "").strip().lower()
            in {"1", "true", "yes", "on"},
            execution_timeout=max(5, min(float(os.getenv("AXIOM_EXECUTION_TIMEOUT", "120")), 1800)),
            allow_preview=os.getenv("AXIOM_ALLOW_PREVIEW", "").strip().lower()
            in {"1", "true", "yes", "on"},
            preview_port=max(1024, min(int(os.getenv("AXIOM_PREVIEW_PORT", "8100")), 65535)),
            allow_mcp=os.getenv("AXIOM_ALLOW_MCP", "").strip().lower()
            in {"1", "true", "yes", "on"},
            mcp_servers=os.getenv("AXIOM_MCP_SERVERS", "").strip(),
            mcp_timeout=max(5, min(float(os.getenv("AXIOM_MCP_TIMEOUT", "120")), 1800)),
            max_provider_attempts=max(1, min(int(os.getenv("AXIOM_MAX_PROVIDER_ATTEMPTS", "3")), 8)),
            provider_retry_base=max(0.0, min(float(os.getenv("AXIOM_PROVIDER_RETRY_BASE", "1")), 30)),
            auto_resume=os.getenv("AXIOM_AUTO_RESUME", "").strip().lower() in {"1", "true", "yes", "on"},
            auto_continue=os.getenv("AXIOM_AUTO_CONTINUE", "").strip().lower()
            in {"1", "true", "yes", "on"},
            max_auto_recovery=max(0, min(int(os.getenv("AXIOM_MAX_AUTO_RECOVERY", "1")), 5)),
        )
