import json
import threading
from pathlib import Path
from typing import Any

from app.atomic_io import write_json_atomic


DEFAULT_TOOL_SETTINGS = {
    "disabled_tools": [],
}


class ToolSettingsStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write(DEFAULT_TOOL_SETTINGS.copy())

    def get(self) -> dict[str, Any]:
        with self.lock:
            return self._read()

    def disabled_tools(self) -> set[str]:
        return set(self.get()["disabled_tools"])

    def update_disabled_tools(self, disabled_tools: list[str] | set[str]) -> dict[str, Any]:
        values = {"disabled_tools": sorted({name for name in disabled_tools if isinstance(name, str) and name})}
        with self.lock:
            self._write(values)
            return values

    def update_enabled_tools(self, all_tool_names: list[str], enabled_tool_names: list[str] | set[str]) -> dict[str, Any]:
        all_names = {name for name in all_tool_names if name}
        enabled_names = {name for name in enabled_tool_names if name}
        return self.update_disabled_tools(all_names - enabled_names)

    def is_enabled(self, tool_name: str) -> bool:
        return tool_name not in self.disabled_tools()

    def _read(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, FileNotFoundError):
            raw = {}

        values = DEFAULT_TOOL_SETTINGS.copy()
        if isinstance(raw, dict):
            values.update(raw)
        disabled_tools = values.get("disabled_tools")
        if not isinstance(disabled_tools, list):
            disabled_tools = []
        values["disabled_tools"] = sorted({name for name in disabled_tools if isinstance(name, str) and name})
        return values

    def _write(self, values: dict[str, Any]) -> None:
        write_json_atomic(self.path, values)
