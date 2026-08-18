import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.atomic_io import write_json_atomic
from app.schedule_types import (
    DEFAULT_TIMEZONE,
    ScheduleValidationError,
    format_schedule,
    get_timezone,
    normalize_schedule,
)


LOCAL_TZ = ZoneInfo(DEFAULT_TIMEZONE)
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
        auto_delete: bool = True,
        timezone: str = DEFAULT_TIMEZONE,
    ) -> dict[str, Any]:
        now = now_local()
        schedule_config = normalize_schedule(schedule_type, schedule_value, timezone)
        validate_schedule_for_write(schedule_config, enabled, now)
        task = {
            "id": uuid4().hex,
            "title": title.strip() or title_from_prompt(prompt),
            "prompt": prompt.strip(),
            "enabled": bool(enabled),
            "auto_delete": bool(auto_delete),
            "schedule_type": schedule_type,
            "schedule_value": schedule_value.strip(),
            "timezone": schedule_config["timezone"],
            "schedule_config": schedule_config,
            "schedule_display": format_schedule(schedule_config),
            "misfire_policy": "skip",
            "max_instances": 1,
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
        auto_delete: bool = True,
        timezone: str = DEFAULT_TIMEZONE,
    ) -> dict[str, Any]:
        with self.lock:
            tasks = self._read()
            task = self._find(tasks, task_id)
            now = now_local()
            schedule_config = normalize_schedule(schedule_type, schedule_value, timezone)
            validate_schedule_for_write(schedule_config, enabled, now)
            task.update(
                {
                    "title": title.strip() or title_from_prompt(prompt),
                    "prompt": prompt.strip(),
                    "enabled": bool(enabled),
                    "auto_delete": bool(auto_delete),
                    "schedule_type": schedule_type,
                    "schedule_value": schedule_value.strip(),
                    "timezone": schedule_config["timezone"],
                    "schedule_config": schedule_config,
                    "schedule_display": format_schedule(schedule_config),
                    "misfire_policy": "skip",
                    "max_instances": 1,
                    "updated_at": now.isoformat(),
                    "error": "",
                }
            )
            task["last_result"] = ""
            task["next_run_at"] = compute_next_run(task, now)
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
                if task.get("last_result") == "running":
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

    def skip_missed_tasks(self, now: datetime | None = None) -> list[str]:
        """Skip executions missed while the application was stopped."""

        current = now or now_local()
        skipped: list[str] = []
        with self.lock:
            tasks = self._read()
            changed = False
            for task in tasks:
                if not task.get("enabled"):
                    continue
                next_run = parse_iso(task.get("next_run_at", ""))
                if not next_run or next_run > current:
                    continue
                task["updated_at"] = current.isoformat()
                task["last_result"] = "skipped"
                task["error"] = "程序未运行时错过了执行时间"
                if task.get("schedule_type") == "once":
                    task["enabled"] = False
                    task["next_run_at"] = ""
                else:
                    task["next_run_at"] = compute_next_run(task, current)
                skipped.append(task["id"])
                changed = True
            if changed:
                self._write(tasks)
        return skipped

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
    task.setdefault("auto_delete", True)
    task.setdefault("schedule_type", "once")
    task.setdefault("schedule_value", "")
    task.setdefault("timezone", DEFAULT_TIMEZONE)
    task.setdefault("misfire_policy", "skip")
    task.setdefault("max_instances", 1)
    task.setdefault("created_at", now)
    task.setdefault("updated_at", task["created_at"])
    task.setdefault("last_run_at", "")
    task.setdefault("next_run_at", "")
    task.setdefault("last_result", "")
    task.setdefault("error", "")
    try:
        schedule_config = normalize_schedule(task["schedule_type"], task["schedule_value"], task["timezone"])
    except ScheduleValidationError as exc:
        task["schedule_config"] = {}
        task["schedule_display"] = "计划无效"
        task["schedule_error"] = str(exc)
    else:
        task["schedule_config"] = schedule_config
        task["timezone"] = schedule_config["timezone"]
        task["schedule_display"] = format_schedule(schedule_config)
        task.pop("schedule_error", None)
    if task["enabled"] and not task["next_run_at"]:
        task["next_run_at"] = compute_next_run(task, now_local())
    return task


def compute_next_run(task: dict[str, Any], after: datetime) -> str:
    try:
        schedule_config = task.get("schedule_config") or normalize_schedule(
            task.get("schedule_type", ""), task.get("schedule_value", ""), task.get("timezone", DEFAULT_TIMEZONE)
        )
    except ScheduleValidationError:
        return ""
    schedule_type = schedule_config.get("type")
    try:
        if schedule_type == "once":
            run_at = datetime.fromisoformat(schedule_config["at"])
            return run_at.isoformat() if run_at >= after else ""
        if schedule_type == "interval":
            return (after + timedelta(seconds=int(schedule_config["interval_seconds"]))).isoformat()
        if schedule_type == "cron":
            from croniter import croniter

            timezone = get_timezone(schedule_config.get("timezone"))
            base = after.astimezone(timezone)
            run_at = croniter(schedule_config["value"], base).get_next(datetime)
            if run_at.tzinfo is None:
                run_at = run_at.replace(tzinfo=timezone)
            return run_at.astimezone(timezone).isoformat()
    except (KeyError, ScheduleValidationError, TypeError, ValueError):
        return ""
    return ""


def validate_schedule_for_write(schedule_config: dict[str, Any], enabled: bool, now: datetime) -> None:
    if not enabled or schedule_config.get("type") != "once":
        return
    run_at = datetime.fromisoformat(schedule_config["at"])
    if run_at <= now:
        raise ScheduleValidationError("单次执行时间必须晚于当前时间")


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
