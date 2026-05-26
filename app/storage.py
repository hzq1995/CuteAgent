import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


class TaskStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("[]", encoding="utf-8")

    def list_conversations(self) -> list[dict[str, Any]]:
        with self.lock:
            return sorted(self._read(), key=lambda item: item["updated_at"], reverse=True)

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        with self.lock:
            return next((item for item in self._read() if item["id"] == conversation_id), None)

    def create_conversation(self, prompt: str) -> dict[str, Any]:
        now = utc_now()
        conversation = {
            "id": uuid4().hex,
            "title": title_from_prompt(prompt),
            "created_at": now,
            "updated_at": now,
            "status": "queued",
            "messages": [
                new_message("user", prompt, "succeeded"),
                new_message("assistant", "", "queued"),
            ],
            "dingding_results": [],
            "error": "",
        }
        with self.lock:
            conversations = self._read()
            conversations.append(conversation)
            self._write(conversations)
        return conversation

    def append_user_message(self, conversation_id: str, content: str) -> dict[str, Any]:
        with self.lock:
            conversations = self._read()
            conversation = self._find(conversations, conversation_id)
            if has_running_message(conversation):
                raise ValueError("Conversation already has a running message")
            message = new_message("user", content, "succeeded")
            conversation["messages"].append(message)
            conversation["status"] = "queued"
            conversation["error"] = ""
            conversation["updated_at"] = utc_now()
            self._write(conversations)
            return message

    def create_assistant_message(self, conversation_id: str) -> dict[str, Any]:
        with self.lock:
            conversations = self._read()
            conversation = self._find(conversations, conversation_id)
            message = new_message("assistant", "", "queued")
            conversation["messages"].append(message)
            conversation["status"] = "queued"
            conversation["updated_at"] = utc_now()
            self._write(conversations)
            return message

    def update_conversation(self, conversation_id: str, **changes: Any) -> dict[str, Any]:
        with self.lock:
            conversations = self._read()
            conversation = self._find(conversations, conversation_id)
            conversation.update(changes)
            conversation["updated_at"] = utc_now()
            self._write(conversations)
            return conversation

    def update_message(self, conversation_id: str, message_id: str, **changes: Any) -> dict[str, Any]:
        with self.lock:
            conversations = self._read()
            conversation = self._find(conversations, conversation_id)
            message = self._find_message(conversation, message_id)
            message.update(changes)
            message["updated_at"] = utc_now()
            conversation["updated_at"] = message["updated_at"]
            self._write(conversations)
            return message

    def append_reasoning(self, conversation_id: str, message_id: str, text: str) -> dict[str, Any]:
        with self.lock:
            conversations = self._read()
            conversation = self._find(conversations, conversation_id)
            message = self._find_message(conversation, message_id)
            message["reasoning_content"] += text
            message["updated_at"] = utc_now()
            conversation["updated_at"] = message["updated_at"]
            self._write(conversations)
            return message

    def append_answer(self, conversation_id: str, message_id: str, text: str) -> dict[str, Any]:
        with self.lock:
            conversations = self._read()
            conversation = self._find(conversations, conversation_id)
            message = self._find_message(conversation, message_id)
            message["content"] += text
            message["updated_at"] = utc_now()
            conversation["updated_at"] = message["updated_at"]
            self._write(conversations)
            return message

    def attach_dingding_result(
        self, conversation_id: str, message_id: str, result: dict[str, Any]
    ) -> dict[str, Any]:
        with self.lock:
            conversations = self._read()
            conversation = self._find(conversations, conversation_id)
            message = self._find_message(conversation, message_id)
            message["dingding_result"] = result
            conversation.setdefault("dingding_results", []).append(
                {"message_id": message_id, "result": result, "created_at": utc_now()}
            )
            conversation["updated_at"] = utc_now()
            self._write(conversations)
            return message

    def has_running_message(self, conversation_id: str) -> bool:
        conversation = self.get_conversation(conversation_id)
        return bool(conversation and has_running_message(conversation))

    def chat_context(self, conversation_id: str, through_message_id: str) -> list[dict[str, str]]:
        conversation = self.get_conversation(conversation_id)
        if not conversation:
            raise KeyError(f"Conversation not found: {conversation_id}")

        messages = []
        for message in conversation["messages"]:
            if message["id"] == through_message_id:
                break
            if message["role"] not in {"user", "assistant"}:
                continue
            if message["role"] == "assistant" and not message["content"].strip():
                continue
            messages.append({"role": message["role"], "content": message["content"]})
        return messages

    def list_tasks(self) -> list[dict[str, Any]]:
        return self.list_conversations()

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        return self.get_conversation(task_id)

    def create_task(self, prompt: str) -> dict[str, Any]:
        return self.create_conversation(prompt)

    def _read(self) -> list[dict[str, Any]]:
        try:
            raw_items = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        return [normalize_conversation(item) for item in raw_items]

    def _write(self, conversations: list[dict[str, Any]]) -> None:
        tmp_path = self.path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(conversations, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self.path)

    @staticmethod
    def _find(conversations: list[dict[str, Any]], conversation_id: str) -> dict[str, Any]:
        for conversation in conversations:
            if conversation["id"] == conversation_id:
                return conversation
        raise KeyError(f"Conversation not found: {conversation_id}")

    @staticmethod
    def _find_message(conversation: dict[str, Any], message_id: str) -> dict[str, Any]:
        for message in conversation["messages"]:
            if message["id"] == message_id:
                return message
        raise KeyError(f"Message not found: {message_id}")


def normalize_conversation(item: dict[str, Any]) -> dict[str, Any]:
    if "messages" in item:
        item.setdefault("title", title_from_prompt(first_user_content(item)))
        item.setdefault("dingding_results", [])
        item.setdefault("error", "")
        item.setdefault("status", infer_status(item["messages"]))
        for message in item["messages"]:
            normalize_message(message)
        return item

    now = item.get("created_at") or utc_now()
    messages = []
    if item.get("prompt"):
        messages.append(
            {
                "id": f"{item['id']}-user",
                "role": "user",
                "content": item.get("prompt", ""),
                "reasoning_content": "",
                "status": "succeeded",
                "created_at": now,
                "updated_at": item.get("updated_at", now),
                "dingding_result": None,
            }
        )
    if item.get("answer") or item.get("reasoning_content"):
        messages.append(
            {
                "id": f"{item['id']}-assistant",
                "role": "assistant",
                "content": item.get("answer", ""),
                "reasoning_content": item.get("reasoning_content", ""),
                "status": item.get("status", "succeeded"),
                "created_at": now,
                "updated_at": item.get("updated_at", now),
                "dingding_result": item.get("dingding_result"),
            }
        )

    return {
        "id": item["id"],
        "title": title_from_prompt(item.get("prompt", "Untitled")),
        "created_at": now,
        "updated_at": item.get("updated_at", now),
        "status": item.get("status", "succeeded"),
        "messages": messages,
        "dingding_results": [
            {"message_id": f"{item['id']}-assistant", "result": item["dingding_result"], "created_at": now}
        ]
        if item.get("dingding_result") is not None
        else [],
        "error": item.get("error", ""),
    }


def normalize_message(message: dict[str, Any]) -> None:
    message.setdefault("id", uuid4().hex)
    message.setdefault("content", "")
    message.setdefault("reasoning_content", "")
    message.setdefault("status", "succeeded")
    message.setdefault("created_at", utc_now())
    message.setdefault("updated_at", message["created_at"])
    message.setdefault("dingding_result", None)


def new_message(role: str, content: str, status: str) -> dict[str, Any]:
    now = utc_now()
    return {
        "id": uuid4().hex,
        "role": role,
        "content": content,
        "reasoning_content": "",
        "status": status,
        "created_at": now,
        "updated_at": now,
        "dingding_result": None,
    }


def has_running_message(conversation: dict[str, Any]) -> bool:
    return any(message["status"] in {"queued", "running"} for message in conversation["messages"])


def infer_status(messages: list[dict[str, Any]]) -> str:
    if any(message.get("status") == "failed" for message in messages):
        return "failed"
    if any(message.get("status") in {"queued", "running"} for message in messages):
        return "running"
    return "succeeded"


def first_user_content(conversation: dict[str, Any]) -> str:
    for message in conversation.get("messages", []):
        if message.get("role") == "user":
            return message.get("content", "")
    return "Untitled"


def title_from_prompt(prompt: str) -> str:
    title = " ".join((prompt or "Untitled").strip().split())
    return title[:36] + ("..." if len(title) > 36 else "")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
