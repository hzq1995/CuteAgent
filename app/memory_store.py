import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.atomic_io import write_json_atomic


LOCAL_TZ = ZoneInfo("Asia/Shanghai")


class MemoryStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("[]", encoding="utf-8")

    def list_memories(self) -> list[dict[str, Any]]:
        with self.lock:
            return sorted(self._read(), key=lambda item: item["updated_at"], reverse=True)

    def get_memory(self, memory_id: str) -> dict[str, Any] | None:
        with self.lock:
            return next((memory for memory in self._read() if memory["id"] == memory_id), None)

    def add_memory(self, content: str) -> dict[str, Any]:
        content = normalize_content(content)
        if not content:
            raise ValueError("Memory content cannot be empty")
        now = now_local().isoformat()
        with self.lock:
            memories = self._read()
            existing = find_by_content(memories, content)
            if existing:
                return {"created": False, "memory": existing}
            memory = {
                "id": generate_memory_id(memories),
                "content": content,
                "created_at": now,
                "updated_at": now,
            }
            memories.append(memory)
            self._write(memories)
            return {"created": True, "memory": memory}

    def update_memory(self, memory_id: str, content: str) -> dict[str, Any]:
        content = normalize_content(content)
        if not content:
            raise ValueError("Memory content cannot be empty")
        with self.lock:
            memories = self._read()
            memory = self._find(memories, memory_id)
            memory["content"] = content
            memory["updated_at"] = now_local().isoformat()
            self._write(memories)
            return {"updated": True, "memory": memory}

    def delete_memory(self, memory_id: str) -> dict[str, Any]:
        with self.lock:
            memories = self._read()
            kept = [memory for memory in memories if memory["id"] != memory_id]
            deleted = len(kept) != len(memories)
            if deleted:
                self._write(kept)
            return {"deleted": deleted, "memory_id": memory_id}

    def _read(self) -> list[dict[str, Any]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, FileNotFoundError):
            return []
        return normalize_memories([memory for memory in raw if isinstance(memory, dict)])

    def _write(self, memories: list[dict[str, Any]]) -> None:
        write_json_atomic(self.path, memories)

    @staticmethod
    def _find(memories: list[dict[str, Any]], memory_id: str) -> dict[str, Any]:
        for memory in memories:
            if memory["id"] == memory_id:
                return memory
        raise KeyError(f"Memory not found: {memory_id}")


def normalize_memories(memories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    used_ids: set[str] = set()
    for memory in memories:
        normalized.append(normalize_memory(memory, used_ids))
    return normalized


def normalize_memory(memory: dict[str, Any], used_ids: set[str] | None = None) -> dict[str, Any]:
    now = now_local().isoformat()
    used_ids = used_ids if used_ids is not None else set()
    memory["id"] = normalize_memory_id(str(memory.get("id", "")), used_ids)
    used_ids.add(memory["id"])
    memory["content"] = normalize_content(str(memory.get("content", "")))
    memory.setdefault("created_at", now)
    memory.setdefault("updated_at", memory["created_at"])
    return memory


def normalize_content(content: str) -> str:
    return " ".join((content or "").strip().split())


def generate_memory_id(memories: list[dict[str, Any]]) -> str:
    used_ids = {str(memory.get("id", "")) for memory in memories}
    while True:
        memory_id = uuid4().hex[:8]
        if memory_id not in used_ids:
            return memory_id


def normalize_memory_id(memory_id: str, used_ids: set[str]) -> str:
    memory_id = "".join(char for char in memory_id.strip().lower() if char.isascii() and char.isalnum())
    if not memory_id:
        return generate_memory_id([{"id": used_id} for used_id in used_ids])
    if len(memory_id) <= 8 and memory_id not in used_ids:
        return memory_id
    for size in range(8, len(memory_id) + 1):
        candidate = memory_id[:size]
        if candidate not in used_ids:
            return candidate
    return generate_memory_id([{"id": used_id} for used_id in used_ids])


def find_by_content(memories: list[dict[str, Any]], content: str) -> dict[str, Any] | None:
    for memory in memories:
        if normalize_content(memory.get("content", "")) == content:
            return memory
    return None


def now_local() -> datetime:
    return datetime.now(LOCAL_TZ)
