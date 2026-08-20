"""Shared limits and serializers for tool output."""

import json
from typing import Any


MAX_TOOL_OUTPUT_CHARS = 12000
TOOL_RESULT_PREVIEW_CHARS = 4000


def serialize_tool_result(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return json.dumps(str(value), ensure_ascii=False, indent=2)


def tool_result_preview(value: Any, max_chars: int = TOOL_RESULT_PREVIEW_CHARS) -> dict[str, Any]:
    serialized = serialize_tool_result(value)
    truncated = len(serialized) > max_chars
    preview = serialized[:max_chars]
    if truncated:
        preview += "\n...[preview truncated]"
    return {
        "text": preview,
        "size_chars": len(serialized),
        "truncated": truncated,
    }


def model_tool_content(value: Any, max_chars: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    try:
        serialized = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        serialized = json.dumps(str(value), ensure_ascii=False)
    if len(serialized) <= max_chars:
        return serialized
    payload = {
        "truncated": True,
        "original_size_chars": len(serialized),
        "preview": "",
    }
    preview_budget = max(0, max_chars - len(json.dumps(payload, ensure_ascii=False)) - 24)
    while True:
        payload["preview"] = serialized[:preview_budget] + "\n...[truncated]"
        encoded = json.dumps(payload, ensure_ascii=False)
        if len(encoded) <= max_chars or preview_budget == 0:
            return encoded
        preview_budget = max(0, preview_budget - (len(encoded) - max_chars))
