import mimetypes
import re
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from app.agent_tools import ToolContext, resolve_workspace_file


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "send_file",
        "description": (
            "Send a file from the CuteHarness workspace to the web page. "
            "If the file is an image it will be shown inline; otherwise the page will show a download link. "
            "The path must be relative to the CuteHarness workspace; absolute paths are not allowed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path to a file inside the CuteHarness workspace. Do not use an absolute path.",
                },
                "display_name": {
                    "type": "string",
                    "description": "Optional filename to show on the page.",
                },
            },
            "required": ["path"],
        },
    },
}


def run(context: ToolContext, path: str, display_name: str = "") -> dict[str, Any]:
    source = resolve_workspace_file(context.base_dir, path)
    file_id = uuid4().hex
    filename = safe_filename(display_name or source.name)
    target_dir = context.base_dir / "data" / "shared_files" / file_id
    target_dir.mkdir(parents=True, exist_ok=False)
    target = target_dir / filename
    shutil.copy2(source, target)

    mime_type, _ = mimetypes.guess_type(filename)
    if not mime_type:
        mime_type, _ = mimetypes.guess_type(source.name)
    mime_type = mime_type or "application/octet-stream"
    return {
        "type": "transferred_file",
        "file": {
            "id": file_id,
            "name": filename,
            "url": f"/files/{file_id}/{quote(filename)}",
            "mime_type": mime_type,
            "size_bytes": target.stat().st_size,
            "is_image": mime_type.startswith("image/"),
        },
    }


def safe_filename(value: str) -> str:
    name = Path(value or "file").name.strip().replace("\\", "_").replace("/", "_")
    name = re.sub(r"[\x00-\x1f\x7f]+", "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        return "file"
    return name[:160]
