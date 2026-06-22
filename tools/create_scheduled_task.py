from typing import Any

from app.agent_tools import ToolContext


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "create_scheduled_task",
        "description": "Create a CuteHarness application scheduled task. schedule_type must be once, daily, or interval_minutes. Remember to remind using send_dingtalk_message in schedule value, to send a message to user.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "prompt": {"type": "string"},
                "schedule_type": {"type": "string", "enum": ["once", "daily", "interval_minutes"]},
                "schedule_value": {"type": "string", "description": "once: YYYY-MM-DD HH:mm, daily: HH:mm, interval_minutes: positive integer."},
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
) -> dict[str, Any]:
    return context.scheduled_tasks.create_task(title, prompt, schedule_type, schedule_value, enabled, auto_delete)
