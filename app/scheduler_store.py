import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.atomic_io import write_json_atomic


LOCAL_TZ = ZoneInfo("Asia/Shanghai")
DATETIME_FORMAT = "%Y-%m-%d %H:%M"
TIME_FORMAT = "%H:%M"


class ScheduledTaskStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("[]", encoding="utf-8")

    def list_tasks(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        with self.lock:
            tasks = self._read()
            if enabled_only:
                tasks = [t for t in tasks if t.get("enabled")]
            return sorted(tasks, key=lambda item: item["created_at"], reverse=True)

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self.lock:
            return next((task for task in self._read() if task["id"] == task_id), None)

    def create_task(
        self,
        title: str,
        prompt: str,
        schedule_type: str,
        schedule_value: str,
        enabled: bool = True,
    ) -> dict[str, Any]:
        now = now_local()
        task = {
            "id": uuid4().hex,
            "title": title.strip() or title_from_prompt(prompt),
            "prompt": prompt.strip(),
            "enabled": bool(enabled),
            "schedule_type": schedule_type,
            "schedule_value": schedule_value.strip(),
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "last_run_at": "",
            "next_run_at": "",
            "last_result": "",
            "error": "",
        }
        task["next_run_at"] = compute_next_run(task, now)
        with self.lock:
            tasks = self._read()
            tasks.append(task)
            self._write(tasks)
        return task

    def update_task(
        self,
        task_id: str,
        title: str,
        prompt: str,
        schedule_type: str,
        schedule_value: str,
        enabled: bool,
    ) -> dict[str, Any]:
        with self.lock:
            tasks = self._read()
            task = self._find(tasks, task_id)
            task.update(
                {
                    "title": title.strip() or title_from_prompt(prompt),
                    "prompt": prompt.strip(),
                    "enabled": bool(enabled),
                    "schedule_type": schedule_type,
                    "schedule_value": schedule_value.strip(),
                    "updated_at": now_local().isoformat(),
                    "error": "",
                }
            )
            task["next_run_at"] = compute_next_run(task, now_local())
            self._write(tasks)
            return task

    def delete_task(self, task_id: str) -> bool:
        with self.lock:
            tasks = self._read()
            kept = [task for task in tasks if task["id"] != task_id]
            if len(kept) == len(tasks):
                return False
            self._write(kept)
            return True

    def claim_due_tasks(self) -> list[dict[str, Any]]:
        now = now_local()
        due: list[dict[str, Any]] = []
        with self.lock:
            tasks = self._read()
            for task in tasks:
                if not task.get("enabled"):
                    continue
                next_run = parse_iso(task.get("next_run_at", ""))
                if next_run is None:
                    task["next_run_at"] = compute_next_run(task, now)
                    next_run = parse_iso(task["next_run_at"])
                if next_run and next_run <= now:
                    task["last_run_at"] = now.isoformat()
                    task["last_result"] = "running"
                    task["error"] = ""
                    if task["schedule_type"] == "once":
                        task["enabled"] = False
                        task["next_run_at"] = ""
                    else:
                        task["next_run_at"] = compute_next_run(task, now + timedelta(seconds=1))
                    task["updated_at"] = now.isoformat()
                    due.append(task.copy())
            self._write(tasks)
        return due

    def mark_result(self, task_id: str, result: str, error: str = "") -> None:
        with self.lock:
            tasks = self._read()
            task = self._find(tasks, task_id)
            task["last_result"] = result
            task["error"] = error
            task["updated_at"] = now_local().isoformat()
            self._write(tasks)

    def mark_interrupted_runs(self) -> None:
        with self.lock:
            tasks = self._read()
            changed = False
            for task in tasks:
                if task.get("last_result") == "running":
                    task["last_result"] = "failed"
                    task["error"] = "Scheduler restarted before this run completed"
                    task["updated_at"] = now_local().isoformat()
                    changed = True
            if changed:
                self._write(tasks)

    def _read(self) -> list[dict[str, Any]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        return [normalize_task(task) for task in raw if isinstance(task, dict)]

    def _write(self, tasks: list[dict[str, Any]]) -> None:
        write_json_atomic(self.path, tasks)

    @staticmethod
    def _find(tasks: list[dict[str, Any]], task_id: str) -> dict[str, Any]:
        for task in tasks:
            if task["id"] == task_id:
                return task
        raise KeyError(f"Scheduled task not found: {task_id}")


def normalize_task(task: dict[str, Any]) -> dict[str, Any]:
    now = now_local().isoformat()
    task.setdefault("id", uuid4().hex)
    task.setdefault("title", title_from_prompt(task.get("prompt", "")))
    task.setdefault("prompt", "")
    task.setdefault("enabled", True)
    task.setdefault("schedule_type", "once")
    task.setdefault("schedule_value", "")
    task.setdefault("created_at", now)
    task.setdefault("updated_at", task["created_at"])
    task.setdefault("last_run_at", "")
    task.setdefault("next_run_at", "")
    task.setdefault("last_result", "")
    task.setdefault("error", "")
    if task["enabled"] and not task["next_run_at"]:
        task["next_run_at"] = compute_next_run(task, now_local())
    return task


def compute_next_run(task: dict[str, Any], after: datetime) -> str:
    schedule_type = task.get("schedule_type")
    schedule_value = (task.get("schedule_value") or "").strip()
    try:
        if schedule_type == "once":
            run_at = datetime.strptime(schedule_value, DATETIME_FORMAT).replace(tzinfo=LOCAL_TZ)
            return run_at.isoformat() if run_at >= after else ""
        if schedule_type == "daily":
            hour, minute = map(int, schedule_value.split(":"))
            run_at = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if run_at <= after:
                run_at += timedelta(days=1)
            return run_at.isoformat()
        if schedule_type == "interval_minutes":
            minutes = max(1, int(schedule_value))
            return (after + timedelta(minutes=minutes)).isoformat()
    except Exception:
        return ""
    return ""


def now_local() -> datetime:
    return datetime.now(LOCAL_TZ)


def parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=LOCAL_TZ)
    except ValueError:
        return None


def title_from_prompt(prompt: str) -> str:
    title = " ".join((prompt or "Untitled").strip().split())
    return title[:36] + ("..." if len(title) > 36 else "")
