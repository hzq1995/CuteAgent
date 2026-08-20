"""向用户发送可交互的问题卡片（支持选项按钮）。"""

from typing import Any

from app.agent_tools import ToolContext


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "ask_user",
        "description": (
            "Send an interactive question card to the user, optionally with clickable options. "
            "The user's answer arrives as the next user message in the conversation. "
            "Use this when you need the user to provide information, make a choice, or confirm a decision. "
            "After calling this tool you should end your turn naturally and wait for the user's reply."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question to display to the user. Keep it short and clear.",
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Up to 6 clickable options. Clicking an option fills the reply with that text.",
                },
                "allow_custom": {
                    "type": "boolean",
                    "description": "Whether the user may also type a free-form answer (default true).",
                },
            },
            "required": ["question"],
        },
    },
}


def run(
    context: ToolContext,
    question: str,
    options: list[str] | None = None,
    allow_custom: bool = True,
) -> dict[str, Any]:
    cleaned_options = [str(option).strip() for option in (options or []) if str(option).strip()]
    return {
        "type": "user_question",
        "question": str(question).strip(),
        "options": cleaned_options[:6],
        "allow_custom": bool(allow_custom),
        "note": "问题卡片已展示给用户，回答将出现在用户的下一条消息中。",
    }
