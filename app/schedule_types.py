from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter


DEFAULT_TIMEZONE = "Asia/Shanghai"
INTERVAL_PATTERN = re.compile(r"^(?P<amount>[1-9]\d*)\s*(?P<unit>[smhd])$", re.IGNORECASE)


class ScheduleValidationError(ValueError):
    """Raised when a scheduled task cannot be represented safely."""


def get_timezone(name: str | None) -> ZoneInfo:
    timezone_name = (name or DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ScheduleValidationError(f"不支持的时区：{timezone_name}") from exc


def parse_interval_seconds(value: str) -> int:
    text = str(value or "").strip().lower()
    match = INTERVAL_PATTERN.fullmatch(text)
    if not match:
        raise ScheduleValidationError("间隔格式应为正整数加单位，例如 30s、5m、2h 或 1d")
    amount = int(match.group("amount"))
    multiplier = {"s": 1, "m": 60, "h": 3600, "d": 86400}[match.group("unit")]
    return amount * multiplier


def _parse_datetime(value: str, timezone: ZoneInfo) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ScheduleValidationError("单次任务必须填写执行时间")
    try:
        parsed = datetime.fromisoformat(text.replace(" ", "T", 1))
    except ValueError as exc:
        raise ScheduleValidationError("单次时间格式应为 YYYY-MM-DD HH:mm") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone)
    return parsed.astimezone(timezone)


def normalize_schedule(
    schedule_type: str,
    schedule_value: str,
    timezone: str | None = DEFAULT_TIMEZONE,
) -> dict[str, Any]:
    """Return the canonical schedule representation used by the store and UI."""

    raw_type = str(schedule_type or "").strip().lower()
    value = str(schedule_value or "").strip()
    timezone_obj = get_timezone(timezone)
    canonical: dict[str, Any] = {
        "type": raw_type,
        "value": value,
        "timezone": timezone_obj.key,
    }

    if raw_type == "daily":
        try:
            hour_text, minute_text = value.split(":", 1)
            hour = int(hour_text)
            minute = int(minute_text)
            if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                raise ValueError
        except (ValueError, TypeError) as exc:
            raise ScheduleValidationError("每天任务时间格式应为 HH:mm") from exc
        canonical.update(
            {
                "type": "cron",
                "value": f"{minute} {hour} * * *",
                "legacy_type": "daily",
                "legacy_value": value,
            }
        )
        return canonical

    if raw_type == "interval_minutes":
        try:
            minutes = int(value)
            if minutes <= 0:
                raise ValueError
        except (ValueError, TypeError) as exc:
            raise ScheduleValidationError("间隔分钟必须是正整数") from exc
        canonical.update(
            {
                "type": "interval",
                "value": f"{minutes}m",
                "interval_seconds": minutes * 60,
                "legacy_type": "interval_minutes",
                "legacy_value": value,
            }
        )
        return canonical

    if raw_type == "once":
        parsed = _parse_datetime(value, timezone_obj)
        canonical.update({"value": parsed.strftime("%Y-%m-%d %H:%M"), "at": parsed.isoformat()})
        return canonical

    if raw_type == "interval":
        canonical["interval_seconds"] = parse_interval_seconds(value)
        return canonical

    if raw_type == "cron":
        if not croniter.is_valid(value):
            raise ScheduleValidationError("Cron 表达式无效，应使用五段式格式，例如 0 9 * * 1-5")
        canonical["value"] = value
        return canonical

    raise ScheduleValidationError("计划类型必须是 once、interval 或 cron")


def format_schedule(schedule: dict[str, Any]) -> str:
    schedule_type = schedule.get("type") or schedule.get("schedule_type")
    value = str(schedule.get("value") or schedule.get("schedule_value") or "").strip()
    legacy_type = schedule.get("legacy_type") or schedule.get("schedule_type")
    if legacy_type == "daily":
        legacy_value = str(schedule.get("legacy_value") or value)
        return f"每天 {legacy_value}"
    if legacy_type == "interval_minutes":
        legacy_value = str(schedule.get("legacy_value") or value)
        return f"每隔 {legacy_value} 分钟"
    if schedule_type == "once":
        return f"单次：{value.replace('T', ' ')}"
    if schedule_type == "interval":
        return f"每隔 {value}"
    if schedule_type == "cron":
        return f"Cron：{value}"
    return f"计划：{value}" if value else "计划未配置"
