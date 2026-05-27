from typing import Any

from app.agent_tools import ToolContext


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "add_memory",
        "description": (
            "Add a long-term CuteHarness memory. Only save key, durable, non-duplicate facts or preferences "
            "that will be useful in future conversations. Do not save temporary chat context, one-off tasks, "
            "irrelevant sensitive data, or information already present in existing memories."
            "Only you call this tool, then you can remember the long-term memory. Otherwise, you will forget it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Concise memory content to persist."},
            },
            "required": ["content"],
        },
    },
}


def run(context: ToolContext, content: str) -> dict[str, Any]:
    return context.memories.add_memory(content)
