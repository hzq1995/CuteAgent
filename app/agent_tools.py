import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.memory_store import MemoryStore
from app.scheduler_store import ScheduledTaskStore
from app.storage import TaskStore
from utils.dingding_robot import DingdingRobot


BUSINESS_NOTICE_PREFIX = "[业务通知]"
MAX_TOOL_OUTPUT_CHARS = 12000


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "run_python",
            "description": "Run Python code on the local machine and return stdout, stderr, exit code, and timeout status. Use print() to get output.",
            "parameters": {
                "type": "object",
                "properties": {"code": {"type": "string", "description": "Python code to execute."}},
                "required": ["code"],
            },
        },
    },
    {
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
    },
    {
        "type": "function",
        "function": {
            "name": "create_scheduled_task",
            "description": "Create a CuteHarness application scheduled task. schedule_type must be once, daily, or interval_minutes. Remember to remind using send_dingtalk_message in schedule value, to send a message to user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "prompt": {"type": "string"},
                    "schedule_type": {"type": "string", "enum": ["once", "daily", "interval_minutes"]},
                    "schedule_value": {"type": "string", "description": "once: YYYY-MM-DD HH:mm, daily: HH:mm, interval_minutes: positive integer."},
                    "enabled": {"type": "boolean"},
                },
                "required": ["title", "prompt", "schedule_type", "schedule_value", "enabled"],
            },
        },
    },
    {
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
    },
    {
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
    },
    {
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
    },
    {
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
    },
    {
        "type": "function",
        "function": {
            "name": "send_dingtalk_message",
            "description": "Send a DingTalk markdown message to user. When we say '发送消息' or other similar words, it means you should call this tool.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["title", "text"],
            },
        },
    },
    {
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
    },
    {
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
    },
]


class AgentToolRunner:
    def __init__(
        self,
        base_dir: Path,
        scheduled_tasks: ScheduledTaskStore,
        memories: MemoryStore,
        task_store: TaskStore,
        python_timeout_seconds: int,
        dingtalk_webhook_url: str = "",
        dingtalk_access_token: str = "",
    ):
        self.base_dir = base_dir
        self.scheduled_tasks = scheduled_tasks
        self.memories = memories
        self.task_store = task_store
        self.python_timeout_seconds = python_timeout_seconds
        self.dingtalk_webhook_url = dingtalk_webhook_url
        self.dingtalk_access_token = dingtalk_access_token
        self._tools: dict[str, Callable[..., Any]] = {
            "run_python": self.run_python,
            "list_scheduled_tasks": self.list_scheduled_tasks,
            "create_scheduled_task": self.create_scheduled_task,
            "delete_scheduled_task": self.delete_scheduled_task,
            "add_memory": self.add_memory,
            "update_memory": self.update_memory,
            "delete_memory": self.delete_memory,
            "send_dingtalk_message": self.send_dingtalk_message,
            "list_conversations": self.list_conversations,
            "get_conversation": self.get_conversation,
        }

    def run(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name not in self._tools:
            return {"ok": False, "error": f"Unknown tool: {name}"}
        try:
            result = self._tools[name](**arguments)
            return {"ok": True, "result": result}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def run_python(self, code: str) -> dict[str, Any]:
        try:
            completed = subprocess.run(
                [sys.executable, "-c", code],
                cwd=self.base_dir,
                capture_output=True,
                text=True,
                timeout=self.python_timeout_seconds,
            )
            return {
                "stdout": truncate(completed.stdout),
                "stderr": truncate(completed.stderr),
                "exit_code": completed.returncode,
                "timed_out": False,
            }
        except subprocess.TimeoutExpired as exc:
            return {
                "stdout": truncate(exc.stdout or ""),
                "stderr": truncate(exc.stderr or ""),
                "exit_code": None,
                "timed_out": True,
            }

    def list_scheduled_tasks(self, enabled_only: bool = True) -> list[dict[str, Any]]:
        return self.scheduled_tasks.list_tasks(enabled_only=enabled_only)

    def create_scheduled_task(
        self,
        title: str,
        prompt: str,
        schedule_type: str,
        schedule_value: str,
        enabled: bool = True,
    ) -> dict[str, Any]:
        return self.scheduled_tasks.create_task(title, prompt, schedule_type, schedule_value, enabled)

    def delete_scheduled_task(self, task_id: str) -> dict[str, Any]:
        return {"deleted": self.scheduled_tasks.delete_task(task_id), "task_id": task_id}

    def add_memory(self, content: str) -> dict[str, Any]:
        return self.memories.add_memory(content)

    def update_memory(self, memory_id: str, content: str) -> dict[str, Any]:
        return self.memories.update_memory(memory_id, content)

    def delete_memory(self, memory_id: str) -> dict[str, Any]:
        return self.memories.delete_memory(memory_id)

    def send_dingtalk_message(self, title: str, text: str) -> dict[str, Any]:
        robot = DingdingRobot(
            webhook_url=self.dingtalk_webhook_url,
            access_token=self.dingtalk_access_token,
        )
        return robot.send_markdown(ensure_business_prefix(title), ensure_business_prefix(text))

    def list_conversations(self, limit: int = 10) -> list[dict[str, Any]]:
        all_conversations = self.task_store.list_conversations()
        return [
            {"id": c["id"], "title": c["title"], "updated_at": c["updated_at"]}
            for c in all_conversations[:limit]
        ]

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        conversation = self.task_store.get_conversation(conversation_id)
        if conversation is None:
            return None
        messages = [
            {"role": m["role"], "content": m["content"]}
            for m in conversation.get("messages", [])
            if m["role"] in ("user", "assistant")
        ]
        return {"id": conversation["id"], "title": conversation["title"], "messages": messages}


def parse_tool_arguments(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("Tool arguments must be a JSON object")
    return parsed


def ensure_business_prefix(value: str) -> str:
    value = value or ""
    return value if value.startswith(BUSINESS_NOTICE_PREFIX) else f"{BUSINESS_NOTICE_PREFIX} {value}".strip()


def truncate(value: str) -> str:
    if len(value) <= MAX_TOOL_OUTPUT_CHARS:
        return value
    return value[:MAX_TOOL_OUTPUT_CHARS] + "\n...[truncated]"
