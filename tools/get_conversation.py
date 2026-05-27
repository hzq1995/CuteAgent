from typing import Any

from app.agent_tools import ToolContext


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "get_conversation",
        "description": "Get the full message content of a specific conversation by its id. Returns the title and all user/assistant messages (tool messages excluded).",
        "parameters": {
            "type": "object",
            "properties": {
                "conversation_id": {"type": "string", "description": "The conversation id to retrieve."},
            },
            "required": ["conversation_id"],
        },
    },
}


def run(context: ToolContext, conversation_id: str) -> dict[str, Any] | None:
    conversation = context.task_store.get_conversation(conversation_id)
    if conversation is None:
        return None
    messages = [
        {"role": m["role"], "content": m["content"]}
        for m in conversation.get("messages", [])
        if m["role"] in ("user", "assistant")
    ]
    return {"id": conversation["id"], "title": conversation["title"], "messages": messages}
