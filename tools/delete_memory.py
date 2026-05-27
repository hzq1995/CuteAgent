from typing import Any

from app.agent_tools import ToolContext


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "delete_memory",
        "description": "Delete a CuteHarness memory by id when the user asks to forget it or it is no longer true.",
        "parameters": {
            "type": "object",
            "properties": {"memory_id": {"type": "string", "description": "Memory id shown in the injected memory list."}},
            "required": ["memory_id"],
        },
    },
}


def run(context: ToolContext, memory_id: str) -> dict[str, Any]:
    return context.memories.delete_memory(memory_id)
