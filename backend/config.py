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
    report_max_tokens: int = 8192
    reasoning_max_tokens: int = 2048
    request_timeout: float = 180
    # One wall-clock budget for a whole model call, retries and the waits between them
    # included. Without one, a 180-second read timeout and three attempts let a single
    # call hold a task for nine minutes with nothing to show for it. 0 disables it.
    model_call_timeout: float = 240
    # A Planner that has produced nothing after this long is unlikely to. A shorter
    # budget lets the run say so instead of leaving the dashboard on a spinner.
    planner_model_call_timeout: float = 150
    # How often a call that is still running writes an updated activity line. One
    # "waiting" line stops being reassuring after a minute: the operator cannot tell a
    # live run from a dead one.
    wait_update_seconds: float = 15.0
    # JEV (TypeSafe System One) answers typed questions with probabilities instead
    # of prose. It is a second provider with its own key and endpoint, and it is
    # optional: an empty base URL means the runtime behaves exactly as before.
    typesafe_api_key: str = ""
    jev_base_url: str = "https://api.typesafe.ai/v1"
    jev_model: str = "jev-1.13.0"
    jev_request_timeout: float = 60
    jev_max_attempts: int = 3
    # Ask JEV whether the finished project satisfies the requirements the task
    # states. Evidence only: it is recorded for the Tester and Reviewer, and it can
    # never approve a task or override a deterministic validation failure.
    jev_evidence: bool = True
    # One wall-clock budget for a whole evidence pass, retries and the waits between
    # them included: a slow decision must never hold a finished task open.
    jev_time_budget: float = 5.0
    # Shadow mode records what JEV would recommend next and never acts on it.
    jev_shadow: bool = False
    jev_shadow_max: int = 3
    jev_shadow_timeout: float = 3.0
    # Used to turn a reported token count into an estimated cost. The API reports
    # tokens, not money, and an unpriced call is recorded as unknown, never as zero.
    jev_input_price_per_mtok: float = 0.042
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
        # JEV has a key of its own and follows the same precedence, so one key can
        # live in .env and the other can be saved by the dashboard.
        process_jev_key = os.environ.get("TYPESAFE_API_KEY", "").strip()
        load_dotenv()
        credentials = Path(os.getenv("AXIOM_CREDENTIALS", ".axiom/credentials.env")).absolute()
        if (not process_key or not process_jev_key) and credentials.is_file():
            load_dotenv(credentials, override=True)
        if process_key:
            os.environ["OPENROUTER_API_KEY"] = process_key
        if process_jev_key:
            os.environ["TYPESAFE_API_KEY"] = process_jev_key
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
            report_max_tokens=max(1024, min(int(os.getenv("AXIOM_REPORT_MAX_TOKENS", "8192")), 32768)),
            reasoning_max_tokens=max(0, min(int(os.getenv("AXIOM_REASONING_MAX_TOKENS", "2048")), 100000)),
            request_timeout=max(30, min(float(os.getenv("AXIOM_REQUEST_TIMEOUT", "180")), 1800)),
            model_call_timeout=max(0.0, min(float(os.getenv("AXIOM_MODEL_CALL_TIMEOUT", "240")), 3600)),
            planner_model_call_timeout=max(
                0.0, min(float(os.getenv("AXIOM_PLANNER_MODEL_CALL_TIMEOUT", "150")), 3600)),
            wait_update_seconds=max(0.05, min(float(os.getenv("AXIOM_WAIT_UPDATE_SECONDS", "15")), 300)),
            typesafe_api_key=os.getenv("TYPESAFE_API_KEY", "").strip(),
            jev_base_url=os.getenv("AXIOM_JEV_BASE_URL", "https://api.typesafe.ai/v1").strip(),
            jev_model=os.getenv("AXIOM_JEV_MODEL", "jev-1.13.0").strip(),
            jev_request_timeout=max(5, min(float(os.getenv("AXIOM_JEV_REQUEST_TIMEOUT", "60")), 600)),
            jev_max_attempts=max(1, min(int(os.getenv("AXIOM_JEV_MAX_ATTEMPTS", "3")), 8)),
            jev_evidence=os.getenv("AXIOM_JEV_EVIDENCE", "1").strip().lower()
            in {"1", "true", "yes", "on"},
            jev_time_budget=max(0.5, min(float(os.getenv("AXIOM_JEV_TIME_BUDGET", "5")), 120)),
            jev_shadow=os.getenv("AXIOM_JEV_SHADOW", "").strip().lower()
            in {"1", "true", "yes", "on"},
            jev_shadow_max=max(1, min(int(os.getenv("AXIOM_JEV_SHADOW_MAX", "3")), 20)),
            jev_shadow_timeout=max(0.5, min(float(os.getenv("AXIOM_JEV_SHADOW_TIMEOUT", "3")), 60)),
            jev_input_price_per_mtok=max(0.0, float(os.getenv("AXIOM_JEV_INPUT_PRICE_PER_MTOK", "0.042"))),
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
