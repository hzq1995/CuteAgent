import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


class TaskStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("[]", encoding="utf-8")

    def list_tasks(self) -> list[dict[str, Any]]:
        with self.lock:
            return sorted(self._read(), key=lambda task: task["created_at"], reverse=True)

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self.lock:
            return next((task for task in self._read() if task["id"] == task_id), None)

    def create_task(self, prompt: str) -> dict[str, Any]:
        now = utc_now()
        task = {
            "id": uuid4().hex,
            "created_at": now,
            "updated_at": now,
            "status": "queued",
            "prompt": prompt,
            "reasoning_content": "",
            "answer": "",
            "error": "",
            "dingding_result": None,
        }
        with self.lock:
            tasks = self._read()
            tasks.append(task)
            self._write(tasks)
        return task

    def update_task(self, task_id: str, **changes: Any) -> dict[str, Any]:
        with self.lock:
            tasks = self._read()
            for task in tasks:
                if task["id"] == task_id:
                    task.update(changes)
                    task["updated_at"] = utc_now()
                    self._write(tasks)
                    return task
        raise KeyError(f"Task not found: {task_id}")

    def append_reasoning(self, task_id: str, text: str) -> dict[str, Any]:
        with self.lock:
            tasks = self._read()
            for task in tasks:
                if task["id"] == task_id:
                    task["reasoning_content"] += text
                    task["updated_at"] = utc_now()
                    self._write(tasks)
                    return task
        raise KeyError(f"Task not found: {task_id}")

    def append_answer(self, task_id: str, text: str) -> dict[str, Any]:
        with self.lock:
            tasks = self._read()
            for task in tasks:
                if task["id"] == task_id:
                    task["answer"] += text
                    task["updated_at"] = utc_now()
                    self._write(tasks)
                    return task
        raise KeyError(f"Task not found: {task_id}")

    def _read(self) -> list[dict[str, Any]]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []

    def _write(self, tasks: list[dict[str, Any]]) -> None:
        tmp_path = self.path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self.path)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
