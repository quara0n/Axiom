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
    task_timeout: float = 600
    max_tool_rounds: int = 12
    allowed_origins: tuple[str, ...] = (
        "http://localhost:3000", "http://127.0.0.1:3000",
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:8000", "http://127.0.0.1:8000",
    )

    @classmethod
    def from_env(cls):
        load_dotenv()
        return cls(
            api_key=os.getenv("OPENROUTER_API_KEY", "").strip(),
            model=os.getenv("AXIOM_MODEL", "openrouter/free"),
            workspace_root=Path(os.getenv("AXIOM_WORKSPACE_ROOT", ".axiom/workspaces")).absolute(),
            database=Path(os.getenv("AXIOM_DATABASE", ".axiom/tasks.sqlite3")).absolute(),
            task_timeout=max(10, min(float(os.getenv("AXIOM_TASK_TIMEOUT", "600")), 3600)),
            max_tool_rounds=max(1, min(int(os.getenv("AXIOM_MAX_TOOL_ROUNDS", "12")), 30)),
        )
