import json
import threading
from pathlib import Path
from typing import Any

from app.atomic_io import write_json_atomic


DEFAULT_APP_SETTINGS = {
    "system_prompt": "",
    "python_timeout_seconds": 30,
    "max_tool_rounds": 5,
}


class AppSettingsStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write(DEFAULT_APP_SETTINGS.copy())

    def get(self) -> dict[str, Any]:
        with self.lock:
            return self._read()

    def update(
        self,
        system_prompt: str,
        python_timeout_seconds: int,
        max_tool_rounds: int,
    ) -> dict[str, Any]:
        python_timeout_seconds = max(1, min(int(python_timeout_seconds), 300))
        max_tool_rounds = max(1, min(int(max_tool_rounds), 20))
        values = {
            "system_prompt": system_prompt.strip(),
            "python_timeout_seconds": python_timeout_seconds,
            "max_tool_rounds": max_tool_rounds,
        }
        with self.lock:
            self._write(values)
            return values

    def _read(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, FileNotFoundError):
            raw = {}
        values = DEFAULT_APP_SETTINGS.copy()
        values.update(raw if isinstance(raw, dict) else {})
        values["python_timeout_seconds"] = int(values.get("python_timeout_seconds") or 30)
        values["max_tool_rounds"] = int(values.get("max_tool_rounds") or 5)
        return values

    def _write(self, values: dict[str, Any]) -> None:
        write_json_atomic(self.path, values)
