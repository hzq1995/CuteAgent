import fnmatch
import os
import re
from pathlib import Path
from typing import Any

from app.agent_tools import ToolContext, resolve_workspace_path


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "grep",
        "description": (
            "Search file contents in the workspace with a regex. Returns matching files, "
            "or matching lines with line numbers, or per-file match counts. "
            "Path must be relative to the workspace. Use this to locate code before edit_file, "
            "instead of parsing shell grep output."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regular expression to search for (Python re syntax).",
                },
                "path": {
                    "type": "string",
                    "description": "Relative file or directory to search. Defaults to the whole workspace.",
                },
                "glob": {
                    "type": "string",
                    "description": "Optional glob to filter file paths, e.g. '*.py' or '*.{ts,tsx}'.",
                },
                "output_mode": {
                    "type": "string",
                    "enum": ["files_with_matches", "content", "count"],
                    "description": "'files_with_matches' (default): list files with matches. 'content': matching lines with line numbers. 'count': match count per file.",
                    "default": "files_with_matches",
                },
                "case_insensitive": {
                    "type": "boolean",
                    "description": "Case-insensitive match. Default false.",
                    "default": False,
                },
                "head_limit": {
                    "type": "integer",
                    "description": "Max files (files_with_matches/count) or matching lines (content) to return. Default 250.",
                    "default": 250,
                },
            },
            "required": ["pattern"],
        },
    },
}


SKIP_DIRS = {".venv", "venv", "__pycache__", ".git", ".idea", ".vscode", "node_modules", "data"}
MAX_LINE_CHARS = 1000


def run(
    context: ToolContext,
    pattern: str,
    path: str = "",
    glob: str = "",
    output_mode: str = "files_with_matches",
    case_insensitive: bool = False,
    head_limit: int = 250,
) -> dict[str, Any]:
    if not pattern:
        raise ValueError("pattern is required")
    if output_mode not in ("files_with_matches", "content", "count"):
        raise ValueError(f"invalid output_mode: {output_mode}")

    try:
        regex = re.compile(pattern, re.IGNORECASE if case_insensitive else 0)
    except re.error as exc:
        raise ValueError(f"invalid regex: {exc}") from exc

    limit = max(1, int(head_limit))
    root = resolve_workspace_path(context.base_dir, path) if path else context.base_dir.resolve()

    files = _iter_files(root)
    if glob:
        patterns = _expand_brace_glob(glob)
        files = (f for f in files if any(fnmatch.fnmatch(f.name, p) or fnmatch.fnmatch(_rel(f, root), p) for p in patterns))

    matched_files = []
    content_lines = []
    total_files = 0
    total_matches = 0

    for file_path in files:
        rel = _rel(file_path, context.base_dir)
        text = _read_text(file_path)
        if text is None:
            continue

        file_matches = 0
        for line_no, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                file_matches += 1
                if output_mode == "content" and len(content_lines) < limit:
                    content_lines.append({"path": rel, "line": line_no, "text": _truncate_line(line)})

        if file_matches:
            total_files += 1
            total_matches += file_matches
            if output_mode in ("files_with_matches", "count"):
                matched_files.append({"path": rel, "matches": file_matches})

        # content mode stops once the line budget is full
        if output_mode == "content" and len(content_lines) >= limit:
            break

    if output_mode == "files_with_matches":
        files_out = [m["path"] for m in matched_files[:limit]]
        return {
            "output_mode": "files_with_matches",
            "files": files_out,
            "file_count": len(files_out),
            "truncated": total_files > len(files_out),
            "pattern": pattern,
        }
    if output_mode == "count":
        files_out = matched_files[:limit]
        return {
            "output_mode": "count",
            "files": files_out,
            "file_count": len(files_out),
            "total_matches": total_matches,
            "truncated": total_files > len(files_out),
            "pattern": pattern,
        }
    # content
    return {
        "output_mode": "content",
        "matches": content_lines,
        "line_count": len(content_lines),
        "file_count": total_files,
        "total_matches": total_matches,
        "truncated": total_matches > len(content_lines),
        "pattern": pattern,
    }


def _iter_files(root: Path):
    if root.is_file():
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            yield Path(dirpath) / name


def _read_text(path: Path) -> str | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw[:8192]:
        return None  # binary file
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace")


def _truncate_line(line: str) -> str:
    if len(line) <= MAX_LINE_CHARS:
        return line
    return line[:MAX_LINE_CHARS] + "...[truncated]"


def _rel(path: Path, base: Path) -> str:
    try:
        rel = path.resolve().relative_to(base.resolve())
    except ValueError:
        rel = path
    # normalize to forward slashes for cross-platform consistency with edit_file paths
    return str(rel).replace("\\", "/")


def _expand_brace_glob(glob: str) -> list[str]:
    """Expand '*.{ts,tsx}' into ['*.ts', '*.tsx']; passthrough otherwise."""
    m = re.match(r"^(.*)\{(.+)\}(.*)$", glob)
    if not m:
        return [glob]
    prefix, opts, suffix = m.group(1), m.group(2), m.group(3)
    return [f"{prefix}{opt.strip()}{suffix}" for opt in opts.split(",") if opt.strip()]
