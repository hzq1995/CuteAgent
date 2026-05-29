from typing import Any

from app.agent_tools import ToolContext, ensure_business_prefix
from utils.dingding_robot import DingdingRobot


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "send_dingtalk_message",
        "description": "Send a DingTalk markdown message to user. When we say '发送消息' or other similar words, it means you should call this tool.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "text": {"type": "string"},
                "file_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional relative file paths inside the CuteHarness workspace. "
                        "Images are appended inline to the DingTalk markdown; other files are appended as links."
                    ),
                },
            },
            "required": ["title", "text"],
        },
    },
}


def run(context: ToolContext, title: str, text: str, file_paths: list[str] | None = None) -> dict[str, Any]:
    robot = DingdingRobot(
        webhook_url=context.dingtalk_webhook_url,
        access_token=context.dingtalk_access_token,
    )
    return robot.send_markdown(
        ensure_business_prefix(title),
        ensure_business_prefix(text),
        file_paths=file_paths,
        base_dir=context.base_dir,
        public_base_url=context.dingtalk_public_base_url,
    )
