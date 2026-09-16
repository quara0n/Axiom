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
    max_tokens: int = 16384
    reasoning_max_tokens: int = 2048
    request_timeout: float = 180
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
            max_tokens=max(1024, min(int(os.getenv("AXIOM_MAX_TOKENS", "16384")), 200000)),
            reasoning_max_tokens=max(0, min(int(os.getenv("AXIOM_REASONING_MAX_TOKENS", "2048")), 100000)),
            request_timeout=max(30, min(float(os.getenv("AXIOM_REQUEST_TIMEOUT", "180")), 1800)),
        )
