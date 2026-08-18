from typing import Any

from app.agent_tools import MAX_TOOL_OUTPUT_CHARS, ToolContext, resolve_workspace_file


DEFAULT_MAX_CHARS = MAX_TOOL_OUTPUT_CHARS


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": (
            "Read a text file from the CuteHarness workspace. The path must be relative. "
            "Use start_line and end_line for large files; line numbers are 1-based and inclusive."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Relative path to a text file inside the CuteHarness workspace.",
                },
                "start_line": {
                    "type": "integer",
                    "description": "First line to return, 1-based. Defaults to 1.",
                    "default": 1,
                },
                "end_line": {
                    "type": "integer",
                    "description": "Last line to return, inclusive. Defaults to the end of the file.",
                },
                "max_chars": {
                    "type": "integer",
                    "description": f"Maximum characters to return. Defaults to {DEFAULT_MAX_CHARS}.",
                    "default": DEFAULT_MAX_CHARS,
                },
            },
            "required": ["file_path"],
        },
    },
}


def run(
    context: ToolContext,
    file_path: str,
    start_line: int = 1,
    end_line: int | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> dict[str, Any]:
    if isinstance(start_line, bool) or int(start_line) < 1:
        raise ValueError("start_line must be a positive integer")
    if end_line is not None and (isinstance(end_line, bool) or int(end_line) < 1):
        raise ValueError("end_line must be a positive integer")
    if end_line is not None and int(end_line) < int(start_line):
        raise ValueError("end_line must be greater than or equal to start_line")
    if isinstance(max_chars, bool) or int(max_chars) < 1:
        raise ValueError("max_chars must be a positive integer")

    start_line = int(start_line)
    end_line = int(end_line) if end_line is not None else None
    max_chars = int(max_chars)
    path = resolve_workspace_file(context.base_dir, file_path)
    raw = path.read_bytes()
    if b"\x00" in raw[:8192]:
        raise ValueError(f"Binary files are not supported: {file_path}")
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"File is not valid UTF-8 text: {file_path}") from exc

    lines = content.splitlines(keepends=True)
    total_lines = len(lines)
    requested_end = end_line if end_line is not None else total_lines
    selected = "".join(lines[start_line - 1 : requested_end])
    truncated = len(selected) > max_chars
    if truncated:
        selected = selected[:max_chars]

    return {
        "path": file_path,
        "content": selected,
        "start_line": start_line,
        "end_line": min(requested_end, total_lines),
        "total_lines": total_lines,
        "truncated": truncated,
    }
