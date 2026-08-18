from typing import Any

from app.agent_tools import ToolContext
from app.schedule_types import DEFAULT_TIMEZONE


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "create_scheduled_task",
        "description": "Create a CuteHarness scheduled task. The schedule describes when the Prompt runs.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "prompt": {"type": "string"},
                "schedule_type": {"type": "string", "enum": ["once", "interval", "cron", "daily", "interval_minutes"]},
                "schedule_value": {"type": "string", "description": "once: YYYY-MM-DD HH:mm; interval: 30s, 5m, 2h, or 1d; cron: five-field expression such as 0 9 * * 1-5; daily and interval_minutes are legacy formats."},
                "timezone": {"type": "string", "description": f"IANA timezone, default {DEFAULT_TIMEZONE}."},
                "enabled": {"type": "boolean"},
                "auto_delete": {"type": "boolean", "description": "Whether to automatically delete the task after it runs (only applies to 'once' type). Defaults to true."},
            },
            "required": ["title", "prompt", "schedule_type", "schedule_value", "enabled"],
        },
    },
}


def run(
    context: ToolContext,
    title: str,
    prompt: str,
    schedule_type: str,
    schedule_value: str,
    enabled: bool = True,
    auto_delete: bool = True,
    timezone: str = DEFAULT_TIMEZONE,
) -> dict[str, Any]:
    return context.scheduled_tasks.create_task(
        title,
        prompt,
        schedule_type,
        schedule_value,
        enabled,
        auto_delete,
        timezone,
    )
