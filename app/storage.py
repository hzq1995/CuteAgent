import copy
import json
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.atomic_io import write_json_atomic
from app.tool_output import model_tool_content


PERSIST_INTERVAL_SECONDS = 0.25
PERSIST_CHAR_THRESHOLD = 4096
CACHE_MAX_SIZE = 32


class TaskStore:
    def __init__(self, dir_path: Path):
        self.dir = dir_path
        self.lock = threading.Lock()
        self.dir.mkdir(parents=True, exist_ok=True)
        self._next_seq = self._compute_next_seq()
        self._cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._pending_chars: dict[str, int] = {}
        self._last_persisted_at: dict[str, float] = {}
        self._flush_timers: dict[str, threading.Timer] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_conversations(self, limit: int = 10) -> list[dict[str, Any]]:
        with self.lock:
            paths = sorted(
                self.dir.glob("*.json"),
                key=lambda p: int(p.stem.split("-")[0]) if p.stem.split("-")[0].isdigit() else 0,
                reverse=True,
            )[:limit]
            conversations = []
            for path in paths:
                try:
                    conversations.append(normalize_conversation(json.loads(path.read_text(encoding="utf-8"))))
                except (json.JSONDecodeError, OSError):
                    pass
            return sorted(conversations, key=lambda item: item["updated_at"], reverse=True)

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        with self.lock:
            return self._read_one(conversation_id)

    def create_conversation(self, prompt: str) -> dict[str, Any]:
        now = utc_now()
        conversation = {
            "id": uuid4().hex[:16],
            "title": title_from_prompt(prompt),
            "created_at": now,
            "updated_at": now,
            "status": "queued",
            "tools_compressed": False,
            "tool_compression_boundary_id": "",
            "messages": [
                new_message("user", prompt, "succeeded"),
                new_message("assistant", "", "queued"),
            ],
            "dingding_results": [],
            "error": "",
        }
        with self.lock:
            seq = self._next_seq
            self._next_seq += 1
            self._write_file(seq, conversation)
        return conversation

    def append_user_message(self, conversation_id: str, content: str) -> dict[str, Any]:
        with self.lock:
            conversation = self._read_one_or_raise(conversation_id)
            if has_running_message(conversation):
                raise ValueError("Conversation already has a running message")
            message = new_message("user", content, "succeeded")
            conversation["messages"].append(message)
            conversation["status"] = "queued"
            conversation["error"] = ""
            conversation["updated_at"] = utc_now()
            self._write_one(conversation)
            return message

    def create_assistant_message(self, conversation_id: str) -> dict[str, Any]:
        with self.lock:
            conversation = self._read_one_or_raise(conversation_id)
            message = new_message("assistant", "", "queued")
            conversation["messages"].append(message)
            conversation["status"] = "queued"
            conversation["updated_at"] = utc_now()
            self._write_one(conversation)
            return message

    def update_conversation(self, conversation_id: str, **changes: Any) -> dict[str, Any]:
        with self.lock:
            conversation = self._read_one_or_raise(conversation_id)
            conversation.update(changes)
            conversation["updated_at"] = utc_now()
            self._write_one(conversation)
            return conversation

    def update_message(self, conversation_id: str, message_id: str, **changes: Any) -> dict[str, Any]:
        with self.lock:
            conversation = self._read_one_or_raise(conversation_id)
            message = self._find_message(conversation, message_id)
            message.update(changes)
            message["updated_at"] = utc_now()
            conversation["updated_at"] = message["updated_at"]
            self._write_one(conversation)
            return message

    def append_reasoning(self, conversation_id: str, message_id: str, text: str) -> dict[str, Any]:
        with self.lock:
            conversation = self._read_one_for_append(conversation_id)
            message = self._find_message(conversation, message_id)
            message["reasoning_content"] += text
            append_text_part(message, "reasoning", text)
            message["updated_at"] = utc_now()
            conversation["updated_at"] = message["updated_at"]
            self._write_one_deferred(conversation, len(text))
            return copy.deepcopy(message)

    def append_answer(self, conversation_id: str, message_id: str, text: str) -> dict[str, Any]:
        with self.lock:
            conversation = self._read_one_for_append(conversation_id)
            message = self._find_message(conversation, message_id)
            message["content"] += text
            append_text_part(message, "answer", text)
            message["updated_at"] = utc_now()
            conversation["updated_at"] = message["updated_at"]
            self._write_one_deferred(conversation, len(text))
            return copy.deepcopy(message)

    def attach_dingding_result(
        self, conversation_id: str, message_id: str, result: dict[str, Any]
    ) -> dict[str, Any]:
        with self.lock:
            conversation = self._read_one_or_raise(conversation_id)
            message = self._find_message(conversation, message_id)
            message["dingding_result"] = result
            conversation.setdefault("dingding_results", []).append(
                {"message_id": message_id, "result": result, "created_at": utc_now()}
            )
            conversation["updated_at"] = utc_now()
            self._write_one(conversation)
            return message

    def attach_tool_calls(
        self, conversation_id: str, message_id: str, tool_calls: list[dict[str, Any]]
    ) -> dict[str, Any]:
        with self.lock:
            conversation = self._read_one_or_raise(conversation_id)
            message = self._find_message(conversation, message_id)
            existing = message.setdefault("tool_calls", [])
            existing.extend(tool_calls)
            message["updated_at"] = utc_now()
            conversation["updated_at"] = message["updated_at"]
            self._write_one(conversation)
            return message

    def append_tool_message(
        self,
        conversation_id: str,
        assistant_message_id: str,
        tool_call_id: str,
        name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        status: str = "succeeded",
    ) -> dict[str, Any]:
        with self.lock:
            conversation = self._read_one_or_raise(conversation_id)
            assistant = self._find_message(conversation, assistant_message_id)
            message = new_message("tool", model_tool_content(result), status)
            message["tool_call_id"] = tool_call_id
            message["name"] = name
            message["arguments"] = arguments
            message["result"] = result
            message["inline_rendered"] = True
            conversation["messages"].append(message)
            append_tool_part(assistant, message)
            conversation["updated_at"] = message["updated_at"]
            self._write_one(conversation)
            return message

    def append_api_message(
        self, conversation_id: str, assistant_message_id: str, api_message: dict[str, Any]
    ) -> dict[str, Any]:
        with self.lock:
            conversation = self._read_one_or_raise(conversation_id)
            assistant = self._find_message(conversation, assistant_message_id)
            assistant.setdefault("api_messages", []).append(api_message)
            assistant["updated_at"] = utc_now()
            conversation["updated_at"] = assistant["updated_at"]
            self._write_one(conversation)
            return assistant

    def has_running_message(self, conversation_id: str) -> bool:
        conversation = self.get_conversation(conversation_id)
        return bool(conversation and has_running_message(conversation))

    def chat_context(self, conversation_id: str, through_message_id: str) -> list[dict[str, Any]]:
        conversation = self.get_conversation(conversation_id)
        if not conversation:
            raise KeyError(f"Conversation not found: {conversation_id}")

        messages = []
        compression_boundary_id = conversation.get("tool_compression_boundary_id", "")
        compression_active = bool(conversation.get("tools_compressed", False) and compression_boundary_id)

        api_tool_call_ids = {
            item.get("tool_call_id")
            for message in conversation["messages"]
            for item in message.get("api_messages", [])
            if item.get("role") == "tool"
        }
        for message in conversation["messages"]:
            if message["id"] == through_message_id:
                break
            if compression_active:
                messages.extend(compressed_model_messages(message))
                if message["id"] == compression_boundary_id:
                    compression_active = False
                continue
            role = message["role"]
            if role not in {"user", "assistant", "tool"}:
                continue
            if role == "assistant" and message.get("api_messages"):
                messages.extend(message["api_messages"])
                continue
            if role == "assistant" and not message["content"].strip() and not message.get("tool_calls"):
                continue
            if role == "tool":
                if not message.get("tool_call_id"):
                    continue
                if message.get("inline_rendered") and message["tool_call_id"] in api_tool_call_ids:
                    continue
                item = {
                    "role": "tool",
                    "tool_call_id": message["tool_call_id"],
                    "content": message["content"],
                }
                messages.append(item)
                continue

            item = model_message_from_stored(message)
            messages.append(item)
        return messages

    def list_tasks(self) -> list[dict[str, Any]]:
        return self.list_conversations()

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        return self.get_conversation(task_id)

    def create_task(self, prompt: str) -> dict[str, Any]:
        return self.create_conversation(prompt)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_next_seq(self) -> int:
        max_seq = 0
        for p in self.dir.glob("*.json"):
            try:
                max_seq = max(max_seq, int(p.stem.split("-")[0]))
            except (ValueError, IndexError):
                pass
        return max_seq + 1

    def _conversation_path(self, conversation_id: str) -> Path | None:
        matches = list(self.dir.glob(f"*-{conversation_id}.json"))
        return matches[0] if matches else None

    def _write_file(self, seq: int, conversation: dict[str, Any]) -> None:
        path = self.dir / f"{seq:06d}-{conversation['id']}.json"
        write_json_atomic(path, conversation)
        self._cancel_flush_timer(conversation["id"])
        self._cache[conversation["id"]] = copy.deepcopy(conversation)
        self._cache.move_to_end(conversation["id"])
        self._pending_chars.pop(conversation["id"], None)
        self._last_persisted_at[conversation["id"]] = time.monotonic()
        self._trim_cache()

    def _read_one(self, conversation_id: str) -> dict[str, Any] | None:
        cached = self._cache.get(conversation_id)
        if cached is not None:
            self._cache.move_to_end(conversation_id)
            return copy.deepcopy(cached)
        path = self._conversation_path(conversation_id)
        if not path:
            return None
        try:
            conversation = normalize_conversation(json.loads(path.read_text(encoding="utf-8")))
            self._cache[conversation_id] = copy.deepcopy(conversation)
            self._cache.move_to_end(conversation_id)
            self._last_persisted_at[conversation_id] = time.monotonic()
            self._trim_cache()
            return copy.deepcopy(conversation)
        except (json.JSONDecodeError, OSError):
            return None

    def _read_one_or_raise(self, conversation_id: str) -> dict[str, Any]:
        conversation = self._read_one(conversation_id)
        if conversation is None:
            raise KeyError(f"Conversation not found: {conversation_id}")
        return conversation

    def _read_one_for_append(self, conversation_id: str) -> dict[str, Any]:
        cached = self._cache.get(conversation_id)
        if cached is not None:
            self._cache.move_to_end(conversation_id)
            return cached
        return self._read_one_or_raise(conversation_id)

    def _write_one(self, conversation: dict[str, Any], *, enforce_cache_limit: bool = True) -> None:
        path = self._conversation_path(conversation["id"])
        if not path:
            raise KeyError(f"Conversation file not found for: {conversation['id']}")
        write_json_atomic(path, conversation)
        self._cancel_flush_timer(conversation["id"])
        cached = self._cache.get(conversation["id"])
        if cached is conversation:
            self._cache.move_to_end(conversation["id"])
        else:
            self._cache[conversation["id"]] = copy.deepcopy(conversation)
            self._cache.move_to_end(conversation["id"])
        self._pending_chars.pop(conversation["id"], None)
        self._last_persisted_at[conversation["id"]] = time.monotonic()
        if enforce_cache_limit:
            self._trim_cache()

    def _write_one_deferred(self, conversation: dict[str, Any], changed_chars: int) -> None:
        conversation_id = conversation["id"]
        cached = self._cache.get(conversation_id)
        if cached is conversation:
            self._cache.move_to_end(conversation_id)
        else:
            self._cache[conversation_id] = copy.deepcopy(conversation)
            self._cache.move_to_end(conversation_id)
        self._pending_chars[conversation_id] = self._pending_chars.get(conversation_id, 0) + changed_chars
        elapsed = time.monotonic() - self._last_persisted_at.get(conversation_id, 0)
        if elapsed >= PERSIST_INTERVAL_SECONDS or self._pending_chars[conversation_id] >= PERSIST_CHAR_THRESHOLD:
            self._write_one(conversation)
            return
        if conversation_id not in self._flush_timers:
            timer = threading.Timer(PERSIST_INTERVAL_SECONDS, self._flush_pending, args=(conversation_id,))
            timer.daemon = True
            self._flush_timers[conversation_id] = timer
            timer.start()

    def _flush_pending(self, conversation_id: str) -> None:
        with self.lock:
            self._flush_timers.pop(conversation_id, None)
            conversation = self._cache.get(conversation_id)
            if conversation is not None and self._pending_chars.get(conversation_id, 0):
                self._write_one(conversation)

    def _cancel_flush_timer(self, conversation_id: str) -> None:
        timer = self._flush_timers.pop(conversation_id, None)
        if timer and timer is not threading.current_thread():
            timer.cancel()

    def _trim_cache(self) -> None:
        while len(self._cache) > CACHE_MAX_SIZE:
            candidate_id = next(
                (
                    conversation_id
                    for conversation_id, conversation in self._cache.items()
                    if not has_running_message(conversation)
                ),
                None,
            )
            if candidate_id is None:
                return

            conversation = self._cache[candidate_id]
            if self._pending_chars.get(candidate_id, 0):
                self._write_one(conversation, enforce_cache_limit=False)
            else:
                self._cancel_flush_timer(candidate_id)
            self._cache.pop(candidate_id, None)
            self._pending_chars.pop(candidate_id, None)
            self._last_persisted_at.pop(candidate_id, None)

    def _read_all(self) -> list[dict[str, Any]]:
        conversations = []
        for path in sorted(self.dir.glob("*.json")):
            try:
                conversations.append(normalize_conversation(json.loads(path.read_text(encoding="utf-8"))))
            except (json.JSONDecodeError, OSError):
                pass
        return conversations

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
        item.setdefault("tools_compressed", False)
        item.setdefault("tool_compression_boundary_id", "")
        item.setdefault("frozen_system_prompt", "")
        # Older versions stored only a permanent boolean. The exact checkpoint
        # cannot be reconstructed, so start those conversations uncompressed
        # and let the next click establish a precise boundary.
        if item["tools_compressed"] and not item["tool_compression_boundary_id"]:
            item["tools_compressed"] = False
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
        "tools_compressed": False,
        "tool_compression_boundary_id": "",
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
    message.setdefault("tool_calls", [])
    message.setdefault("tool_call_id", "")
    message.setdefault("name", "")
    message.setdefault("arguments", {})
    message.setdefault("result", None)
    message.setdefault("parts", [])
    message.setdefault("inline_rendered", False)
    message.setdefault("api_messages", [])
    message.setdefault("attachments", [])


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
        "parts": [],
        "attachments": [],
    }


def model_message_from_stored(message: dict[str, Any]) -> dict[str, Any]:
    item: dict[str, Any] = {"role": message["role"], "content": message["content"]}
    if message["role"] == "assistant":
        item["reasoning_content"] = message.get("reasoning_content", "")
        if message.get("tool_calls"):
            item["tool_calls"] = message["tool_calls"]
    return item


def compressed_model_messages(message: dict[str, Any]) -> list[dict[str, Any]]:
    role = message.get("role")
    if role == "user":
        content = message.get("content", "") or ""
        return [{"role": "user", "content": content}]
    if role != "assistant":
        return []

    api_messages = message.get("api_messages") or []
    if api_messages:
        result = [
            {"role": "assistant", "content": api_message.get("content", "") or ""}
            for api_message in api_messages
            if api_message.get("role") == "assistant" and (api_message.get("content", "") or "").strip()
        ]
        if result:
            return result

    content = message.get("content", "") or ""
    return [{"role": "assistant", "content": content}] if content.strip() else []


def append_text_part(message: dict[str, Any], kind: str, text: str) -> None:
    parts = message.setdefault("parts", [])
    if parts and parts[-1].get("type") == kind:
        parts[-1]["content"] = parts[-1].get("content", "") + text
        return
    parts.append({"type": kind, "content": text})


def append_tool_part(assistant: dict[str, Any], tool_message: dict[str, Any]) -> None:
    assistant.setdefault("parts", []).append(
        {
            "type": "tool",
            "tool_message_id": tool_message["id"],
            "tool_call_id": tool_message.get("tool_call_id", ""),
            "name": tool_message.get("name", ""),
            "status": tool_message.get("status", ""),
            "arguments": tool_message.get("arguments", {}),
            "result": tool_message.get("result"),
        }
    )


def has_running_message(conversation: dict[str, Any]) -> bool:
    return any(message["status"] in {"queued", "running"} for message in conversation["messages"])


def infer_status(messages: list[dict[str, Any]]) -> str:
    if any(message.get("status") == "failed" for message in messages):
        return "failed"
    if any(message.get("status") == "cancelled" for message in messages):
        return "cancelled"
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
