import json
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from app.memory_store import MemoryStore
from app.scheduler_store import ScheduledTaskStore
from app.storage import TaskStore


BUSINESS_NOTICE_PREFIX = "[业务通知]"
MAX_TOOL_OUTPUT_CHARS = 12000
DEFAULT_TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"


@dataclass(frozen=True)
class ToolContext:
    base_dir: Path
    scheduled_tasks: ScheduledTaskStore
    memories: MemoryStore
    task_store: TaskStore
    python_timeout_seconds: int
    dingtalk_webhook_url: str = ""
    dingtalk_access_token: str = ""


@dataclass(frozen=True)
class LoadedTool:
    definition: dict[str, Any]
    runner: Callable[..., Any]
    path: Path


@dataclass(frozen=True)
class ToolRegistry:
    tools: dict[str, LoadedTool]

    @property
    def definitions(self) -> list[dict[str, Any]]:
        return [tool.definition for tool in self.tools.values()]


class AgentToolRunner:
    def __init__(
        self,
        base_dir: Path,
        scheduled_tasks: ScheduledTaskStore,
        memories: MemoryStore,
        task_store: TaskStore,
        python_timeout_seconds: int,
        dingtalk_webhook_url: str = "",
        dingtalk_access_token: str = "",
        tools_dir: Path = DEFAULT_TOOLS_DIR,
        registry: ToolRegistry | None = None,
        disabled_tools: Iterable[str] | None = None,
    ):
        self.context = ToolContext(
            base_dir=base_dir,
            scheduled_tasks=scheduled_tasks,
            memories=memories,
            task_store=task_store,
            python_timeout_seconds=python_timeout_seconds,
            dingtalk_webhook_url=dingtalk_webhook_url,
            dingtalk_access_token=dingtalk_access_token,
        )
        self.registry = registry or load_tools(tools_dir)
        self.disabled_tools = set(disabled_tools or [])

    @property
    def definitions(self) -> list[dict[str, Any]]:
        return [
            tool.definition
            for name, tool in self.registry.tools.items()
            if name not in self.disabled_tools
        ]

    def run(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool = self.registry.tools.get(name)
        if tool is None:
            return {"ok": False, "error": f"Unknown tool: {name}"}
        if name in self.disabled_tools:
            return {"ok": False, "error": f"Tool is disabled: {name}"}
        try:
            result = tool.runner(self.context, **arguments)
            return {"ok": True, "result": result}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


def load_tools(tools_dir: Path = DEFAULT_TOOLS_DIR) -> ToolRegistry:
    tools: dict[str, LoadedTool] = {}
    if not tools_dir.exists():
        return ToolRegistry(tools={})

    for path in sorted(tools_dir.glob("*.py")):
        if path.name.startswith("_"):
            continue
        module = import_tool_module(path)
        definition = validate_tool_definition(path, getattr(module, "TOOL_DEFINITION", None))
        runner = getattr(module, "run", None)
        if not callable(runner):
            raise ValueError(f"{path} must export callable run(context, **kwargs)")

        name = definition["function"]["name"]
        if name in tools:
            raise ValueError(f"Duplicate tool name {name!r} in {path} and {tools[name].path}")
        tools[name] = LoadedTool(definition=definition, runner=runner, path=path)

    return ToolRegistry(tools=tools)


def import_tool_module(path: Path) -> ModuleType:
    module_name = f"_cuteharness_tool_{path.stem}_{uuid.uuid4().hex}"
    module = ModuleType(module_name)
    module.__file__ = str(path)
    source = path.read_text(encoding="utf-8")
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module


def validate_tool_definition(path: Path, definition: Any) -> dict[str, Any]:
    if not isinstance(definition, dict):
        raise ValueError(f"{path} must export TOOL_DEFINITION as a dict")
    if definition.get("type") != "function":
        raise ValueError(f"{path} TOOL_DEFINITION.type must be 'function'")
    function = definition.get("function")
    if not isinstance(function, dict):
        raise ValueError(f"{path} TOOL_DEFINITION.function must be a dict")
    name = function.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"{path} TOOL_DEFINITION.function.name must be a non-empty string")
    parameters = function.get("parameters")
    if not isinstance(parameters, dict):
        raise ValueError(f"{path} TOOL_DEFINITION.function.parameters must be a dict")
    return definition


def parse_tool_arguments(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("Tool arguments must be a JSON object")
    return parsed


def ensure_business_prefix(value: str) -> str:
    value = value or ""
    return value if value.startswith(BUSINESS_NOTICE_PREFIX) else f"{BUSINESS_NOTICE_PREFIX} {value}".strip()


def truncate(value: str | bytes | None) -> str:
    if value is None:
        value = ""
    if isinstance(value, bytes):
        value = value.decode(errors="replace")
    if len(value) <= MAX_TOOL_OUTPUT_CHARS:
        return value
    return value[:MAX_TOOL_OUTPUT_CHARS] + "\n...[truncated]"
