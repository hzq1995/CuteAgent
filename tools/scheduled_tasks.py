from typing import Any

from app.agent_tools import ToolContext


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "list_scheduled_tasks",
        "description": "List CuteHarness application scheduled tasks. By default only returns enabled tasks. Only list all tasks if user specifies.",
        "parameters": {
            "type": "object",
            "properties": {
                "enabled_only": {"type": "boolean", "description": "If true (default), only return enabled tasks. If false, return all tasks."},
            },
            "required": [],
        },
    },
}


def run(context: ToolContext, enabled_only: bool = True) -> list[dict[str, Any]]:
    return context.scheduled_tasks.list_tasks(enabled_only=enabled_only)
