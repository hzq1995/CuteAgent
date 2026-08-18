import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.agent_tools import ToolContext


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "apply_patch",
        "description": (
            "Apply a structured patch to files inside the CuteHarness workspace. "
            "Use the format starting with '*** Begin Patch' and ending with '*** End Patch'. "
            "It supports '*** Add File:', '*** Update File:', and '*** Delete File:' operations. "
            "Update hunks use context lines prefixed with a space, removed lines with '-', "
            "and added lines with '+'. Paths must be relative."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "patch": {
                    "type": "string",
                    "description": "A complete CuteHarness patch, including Begin Patch and End Patch markers.",
                },
            },
            "required": ["patch"],
        },
    },
}


@dataclass(frozen=True)
class PatchOperation:
    operation: str
    file_path: str
    body: tuple[str, ...]


@dataclass(frozen=True)
class PreparedOperation:
    operation: str
    file_path: str
    path: Path
    content: str | None
    hunk_count: int = 0


def run(context: ToolContext, patch: str) -> dict[str, Any]:
    operations = _parse_patch(patch)
    prepared = [_prepare_operation(context.base_dir, operation) for operation in operations]

    for operation in prepared:
        if operation.operation == "delete":
            operation.path.unlink()
        else:
            _write_text_atomic(operation.path, operation.content or "")

    return {
        "applied": True,
        "files": [
            {
                "path": operation.file_path,
                "operation": operation.operation,
                **({"hunks": operation.hunk_count} if operation.operation == "update" else {}),
            }
            for operation in prepared
        ],
    }


def _parse_patch(patch: str) -> list[PatchOperation]:
    if not isinstance(patch, str) or not patch.strip():
        raise ValueError("patch is required")

    lines = patch.splitlines()
    if not lines or lines[0].strip() != "*** Begin Patch":
        raise ValueError("patch must start with *** Begin Patch")
    if lines[-1].strip() != "*** End Patch":
        raise ValueError("patch must end with *** End Patch")

    operations: list[PatchOperation] = []
    seen_paths: set[str] = set()
    index = 1
    while index < len(lines) - 1:
        line = lines[index]
        if not line.strip():
            index += 1
            continue

        operation, marker = _parse_operation_marker(line)
        if operation is None:
            raise ValueError(f"Unexpected patch line: {line}")
        file_path = marker.strip()
        if not file_path:
            raise ValueError("Patch operation is missing a file path")
        normalized_path = file_path.replace("\\", "/")
        if normalized_path in seen_paths:
            raise ValueError(f"A patch may modify each file only once: {file_path}")
        seen_paths.add(normalized_path)

        index += 1
        body: list[str] = []
        while index < len(lines) - 1 and not _is_operation_marker(lines[index]):
            body.append(lines[index])
            index += 1
        operations.append(PatchOperation(operation, file_path, tuple(body)))

    if not operations:
        raise ValueError("patch contains no file operations")
    return operations


def _parse_operation_marker(line: str) -> tuple[str | None, str]:
    for prefix, operation in (
        ("*** Add File:", "add"),
        ("*** Update File:", "update"),
        ("*** Delete File:", "delete"),
    ):
        if line.startswith(prefix):
            return operation, line[len(prefix) :]
    return None, ""


def _is_operation_marker(line: str) -> bool:
    operation, _ = _parse_operation_marker(line)
    return operation is not None


def _prepare_operation(base_dir: Path, operation: PatchOperation) -> PreparedOperation:
    path = _resolve_target(base_dir, operation.file_path)
    if operation.operation == "add":
        if path.exists():
            raise ValueError(f"File already exists: {operation.file_path}")
        content = _parse_added_file(operation.body, operation.file_path)
        return PreparedOperation("add", operation.file_path, path, content)

    if not path.exists():
        raise FileNotFoundError(f"File not found: {operation.file_path}")
    if not path.is_file():
        raise ValueError(f"Path is not a file: {operation.file_path}")
    if operation.operation == "delete":
        if operation.body:
            raise ValueError(f"Delete operation must not contain patch content: {operation.file_path}")
        return PreparedOperation("delete", operation.file_path, path, None)

    original = _read_text(path, operation.file_path)
    content, hunk_count = _apply_update(original, operation.body, operation.file_path)
    return PreparedOperation("update", operation.file_path, path, content, hunk_count)


def _resolve_target(base_dir: Path, raw_path: str) -> Path:
    candidate = Path(raw_path.strip())
    if candidate.is_absolute():
        raise ValueError("path must be relative to the CuteHarness workspace; absolute paths are not allowed")
    base = base_dir.resolve()
    resolved = (base / candidate).resolve()
    if resolved != base and base not in resolved.parents:
        raise ValueError("Only files inside the CuteHarness workspace can be accessed")
    return resolved


def _parse_added_file(body: tuple[str, ...], file_path: str) -> str:
    if any(not line.startswith("+") for line in body):
        raise ValueError(f"Add operation lines must start with '+': {file_path}")
    if not body:
        return ""
    return "\n".join(line[1:] for line in body) + "\n"


def _apply_update(original: str, body: tuple[str, ...], file_path: str) -> tuple[str, int]:
    hunks = _parse_hunks(body, file_path)
    newline = "\r\n" if "\r\n" in original else "\n"
    has_final_newline = original.endswith(("\n", "\r"))
    lines = original.splitlines()
    search_from = 0

    for hunk in hunks:
        old_lines = [line[1:] for line in hunk if line.startswith((" ", "-"))]
        new_lines = [line[1:] for line in hunk if line.startswith((" ", "+"))]
        if not old_lines:
            raise ValueError(f"Update hunk must contain context or removed lines: {file_path}")
        matches = [
            index
            for index in range(search_from, len(lines) - len(old_lines) + 1)
            if lines[index : index + len(old_lines)] == old_lines
        ]
        if not matches:
            raise ValueError(f"Could not find update hunk context in {file_path}")
        if len(matches) > 1:
            raise ValueError(f"Update hunk context is ambiguous in {file_path}")
        start = matches[0]
        lines[start : start + len(old_lines)] = new_lines
        search_from = start + len(new_lines)

    content = newline.join(lines)
    if has_final_newline and lines:
        content += newline
    return content, len(hunks)


def _parse_hunks(body: tuple[str, ...], file_path: str) -> list[tuple[str, ...]]:
    hunks: list[list[str]] = []
    current: list[str] = []
    saw_hunk_marker = False
    for line in body:
        if line.startswith("@@"):
            saw_hunk_marker = True
            if current:
                hunks.append(current)
                current = []
            continue
        if line == "\\ No newline at end of file":
            continue
        if not line or line[0] not in " +-":
            raise ValueError(f"Invalid update hunk line in {file_path}: {line}")
        current.append(line)
    if current:
        hunks.append(current)
    if not saw_hunk_marker and len(hunks) > 1:
        raise ValueError(f"Invalid update patch in {file_path}")
    if not hunks:
        raise ValueError(f"Update operation contains no hunks: {file_path}")
    return [tuple(hunk) for hunk in hunks]


def _read_text(path: Path, file_path: str) -> str:
    raw = path.read_bytes()
    if b"\x00" in raw[:8192]:
        raise ValueError(f"Binary files are not supported: {file_path}")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"File is not valid UTF-8 text: {file_path}") from exc


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        try:
            if temporary.exists():
                temporary.unlink()
        except OSError:
            pass
