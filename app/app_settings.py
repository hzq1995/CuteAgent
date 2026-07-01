import json
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.atomic_io import write_json_atomic
from app.llm_config import DEFAULT_MODEL, DEFAULT_PROVIDER, normalize_provider_model


DEFAULT_APP_SETTINGS = {
    "llm_provider": DEFAULT_PROVIDER,
    "llm_model": DEFAULT_MODEL,
    "system_prompt": "",
    "python_timeout_seconds": 60,
    "max_tool_rounds": 5,
    "custom_models": [],
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
        python_timeout_seconds = max(1, min(int(python_timeout_seconds), 36000))
        max_tool_rounds = max(1, min(int(max_tool_rounds), 200))
        with self.lock:
            values = self._read()
            custom_models = values["custom_models"]
            llm_provider, llm_model = normalize_provider_model(llm_provider, llm_model, custom_models)
            values.update(
                {
                    "llm_provider": llm_provider,
                    "llm_model": llm_model,
                    "system_prompt": system_prompt.strip(),
                    "python_timeout_seconds": python_timeout_seconds,
                    "max_tool_rounds": max_tool_rounds,
                    "custom_models": custom_models,
                }
            )
            self._write(values)
            return values

    def add_custom_model(self, name: str, base_url: str, model: str, api_key: str) -> dict[str, Any]:
        custom_model = normalize_custom_model(
            {
                "id": f"custom_{uuid4().hex}",
                "name": name,
                "base_url": base_url,
                "model": model,
                "api_key": api_key,
            }
        )
        with self.lock:
            values = self._read()
            values["custom_models"].append(custom_model)
            values["llm_provider"] = custom_model["id"]
            values["llm_model"] = custom_model["model"]
            self._write(values)
            return values

    def delete_custom_model(self, model_id: str) -> dict[str, Any]:
        with self.lock:
            values = self._read()
            values["custom_models"] = [
                custom_model for custom_model in values["custom_models"] if custom_model["id"] != model_id
            ]
            values["llm_provider"], values["llm_model"] = normalize_provider_model(
                values["llm_provider"],
                values["llm_model"],
                values["custom_models"],
            )
            self._write(values)
            return values

    def _read(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, FileNotFoundError):
            raw = {}
        values = DEFAULT_APP_SETTINGS.copy()
        values.update(raw if isinstance(raw, dict) else {})
        values["custom_models"] = normalize_custom_models(values.get("custom_models"))
        values["llm_provider"], values["llm_model"] = normalize_provider_model(
            str(values.get("llm_provider") or ""),
            str(values.get("llm_model") or ""),
            values["custom_models"],
        )
        values["python_timeout_seconds"] = int(values.get("python_timeout_seconds") or 30)
        values["max_tool_rounds"] = int(values.get("max_tool_rounds") or 5)
        return values

    def _write(self, values: dict[str, Any]) -> None:
        write_json_atomic(self.path, values)


def normalize_custom_models(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    models = []
    seen_ids: set[str] = set()
    for item in value:
        try:
            custom_model = normalize_custom_model(item)
        except ValueError:
            continue
        if custom_model["id"] in seen_ids:
            continue
        seen_ids.add(custom_model["id"])
        models.append(custom_model)
    return models


def normalize_custom_model(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("custom model must be an object")
    model_id = str(value.get("id") or "").strip()
    name = str(value.get("name") or "").strip()
    base_url = str(value.get("base_url") or "").strip().rstrip("/")
    model = str(value.get("model") or "").strip()
    api_key = str(value.get("api_key") or "").strip()
    if not model_id.startswith("custom_"):
        raise ValueError("custom model id is invalid")
    if not base_url.startswith(("http://", "https://")):
        raise ValueError("custom model base_url must start with http:// or https://")
    if not model:
        raise ValueError("custom model name is required")
    if not api_key:
        raise ValueError("custom model api_key is required")
    return {
        "id": model_id,
        "name": name or model,
        "base_url": base_url,
        "model": model,
        "api_key": api_key,
    }
