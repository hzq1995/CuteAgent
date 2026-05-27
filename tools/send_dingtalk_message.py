from typing import Any

from app.agent_tools import ToolContext, ensure_business_prefix
from utils.dingding_robot import DingdingRobot


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "send_dingtalk_message",
        "description": "Send a DingTalk markdown message to user. When we say '鍙戦€佹秷鎭? or other similar words, it means you should call this tool.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["title", "text"],
        },
    },
}


def run(context: ToolContext, title: str, text: str) -> dict[str, Any]:
    robot = DingdingRobot(
        webhook_url=context.dingtalk_webhook_url,
        access_token=context.dingtalk_access_token,
    )
    return robot.send_markdown(ensure_business_prefix(title), ensure_business_prefix(text))
