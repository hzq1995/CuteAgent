from typing import Any

from app.agent_tools import ToolContext


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "list_conversations",
        "description": "List recent CuteHarness conversation history. Returns id, title, and updated_at for each conversation, sorted by last updated time descending.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Maximum number of conversations to return. Default is 10."},
            },
            "required": [],
        },
    },
}


def run(context: ToolContext, limit: int = 10) -> list[dict[str, Any]]:
    all_conversations = context.task_store.list_conversations()
    return [
        {"id": c["id"], "title": c["title"], "updated_at": c["updated_at"]}
        for c in all_conversations[:limit]
    ]
