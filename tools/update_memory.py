from typing import Any

from app.agent_tools import ToolContext


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "update_memory",
        "description": "Update an existing CuteHarness memory by id when the remembered fact or preference changes.",
        "parameters": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string", "description": "Memory id shown in the injected memory list."},
                "content": {"type": "string", "description": "Replacement memory content."},
            },
            "required": ["memory_id", "content"],
        },
    },
}


def run(context: ToolContext, memory_id: str, content: str) -> dict[str, Any]:
    return context.memories.update_memory(memory_id, content)
