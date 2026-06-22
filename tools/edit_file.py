import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.agent_tools import ToolContext, resolve_workspace_file


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "edit_file",
        "description": (
            "Edit an existing file in the workspace by replacing an exact string. "
            "Path must be relative. old_string must match verbatim; if not unique, set replace_all=true. "
            "Use run_python to create new files."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Relative path to the file inside the CuteHarness workspace.",
                },
                "old_string": {
                    "type": "string",
                    "description": "The exact text to replace. Must match the file verbatim, including whitespace and newlines. Must be non-empty and differ from new_string.",
                },
                "new_string": {
                    "type": "string",
                    "description": "The text to replace old_string with. May be empty to delete the matched text.",
                },
                "replace_all": {
                    "type": "boolean",
                    "description": "If true, replace every occurrence of old_string. If false (default), old_string must appear exactly once.",
                    "default": False,
                },
            },
            "required": ["file_path", "old_string", "new_string"],
        },
    },
}


def run(
    context: ToolContext,
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> dict[str, Any]:
    if not old_string:
        raise ValueError("old_string must be non-empty")
    if old_string == new_string:
        raise ValueError("new_string must differ from old_string")

    target = resolve_workspace_file(context.base_dir, file_path)

    # newline="" preserves the original byte-level line endings so the match is
    # exact and unrelated lines are not rewritten.
    with target.open("r", encoding="utf-8", newline="") as handle:
        content = handle.read()

    count = content.count(old_string)
    if count == 0:
        raise ValueError(f"old_string not found in {file_path}")
    if not replace_all and count > 1:
        raise ValueError(
            f"old_string is not unique in {file_path} (found {count} occurrences); "
            "pass replace_all=true to replace all, or include more surrounding context"
        )

    new_content = (
        content.replace(old_string, new_string)
        if replace_all
        else content.replace(old_string, new_string, 1)
    )
    _write_text_atomic(target, new_content)

    replacements = count if replace_all else 1
    return {
        "path": file_path,
        "replacements": replacements,
        "message": f"Edited {file_path}: {replacements} replacement(s).",
    }


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
