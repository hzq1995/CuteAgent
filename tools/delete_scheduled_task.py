from typing import Any

from app.agent_tools import ToolContext


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "delete_scheduled_task",
        "description": "Delete a CuteHarness application scheduled task by id.",
        "parameters": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        },
    },
}


def run(context: ToolContext, task_id: str) -> dict[str, Any]:
    return {"deleted": context.scheduled_tasks.delete_task(task_id), "task_id": task_id}
