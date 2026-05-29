import json
import threading
from pathlib import Path
from typing import Any

from app.atomic_io import write_json_atomic
from app.llm_config import DEFAULT_MODEL, DEFAULT_PROVIDER, normalize_provider_model


DEFAULT_APP_SETTINGS = {
    "llm_provider": DEFAULT_PROVIDER,
    "llm_model": DEFAULT_MODEL,
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
        llm_provider: str,
        llm_model: str,
        system_prompt: str,
        python_timeout_seconds: int,
        max_tool_rounds: int,
    ) -> dict[str, Any]:
        llm_provider, llm_model = normalize_provider_model(llm_provider, llm_model)
        python_timeout_seconds = max(1, min(int(python_timeout_seconds), 300))
        max_tool_rounds = max(1, min(int(max_tool_rounds), 20))
        values = {
            "llm_provider": llm_provider,
            "llm_model": llm_model,
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
        values["llm_provider"], values["llm_model"] = normalize_provider_model(
            str(values.get("llm_provider") or ""),
            str(values.get("llm_model") or ""),
        )
        values["python_timeout_seconds"] = int(values.get("python_timeout_seconds") or 30)
        values["max_tool_rounds"] = int(values.get("max_tool_rounds") or 5)
        return values

    def _write(self, values: dict[str, Any]) -> None:
        write_json_atomic(self.path, values)
