import base64
from typing import Any

from openai import OpenAI

from app.agent_tools import ToolContext, resolve_workspace_file
from app.image_support import MAX_IMAGE_BYTES, MAX_IMAGES, image_mime_type


TOOL_NAME = "view_images"
MODEL_NAME = "mimo-v2.5"
TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Analyze one or more uploaded images with Xiaomi MiMo V2.5. "
            "Use this tool when the user message contains image paths and asks about their visual content. "
            "image_paths must be workspace-relative paths copied from the user's uploaded files; "
            "do not use absolute paths or invent paths. Return the visual analysis as text."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "image_paths": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_IMAGES,
                    "items": {
                        "type": "string",
                        "description": "Workspace-relative path of an uploaded image.",
                    },
                    "description": f"One to {MAX_IMAGES} workspace-relative image paths.",
                },
                "prompt": {
                    "type": "string",
                    "description": "The user's visual analysis instruction or question.",
                },
            },
            "required": ["image_paths", "prompt"],
        },
    },
}


def run(context: ToolContext, image_paths: list[str], prompt: str) -> str:
    if not isinstance(image_paths, list) or not image_paths:
        raise ValueError("image_paths must contain at least one image path")
    if len(image_paths) > MAX_IMAGES:
        raise ValueError(f"At most {MAX_IMAGES} images can be analyzed at once")

    cleaned_prompt = (prompt or "").strip()
    if not cleaned_prompt:
        raise ValueError("prompt is required")
    if not context.mimo_api_key:
        raise RuntimeError("MiMo API key is not configured")

    content: list[dict[str, Any]] = []
    for raw_path in image_paths:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("image_paths must contain non-empty strings")
        path = resolve_workspace_file(context.base_dir, raw_path)
        mime_type = image_mime_type(path.name)
        if not mime_type:
            supported = ", ".join(sorted({".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"}))
            raise ValueError(f"Unsupported image format for {raw_path}; supported formats: {supported}")

        size_bytes = path.stat().st_size
        if size_bytes > MAX_IMAGE_BYTES:
            raise ValueError(
                f"Image is too large: {raw_path} exceeds {MAX_IMAGE_BYTES // (1024 * 1024)} MB"
            )

        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
            }
        )

    content.append({"type": "text", "text": cleaned_prompt})
    client = OpenAI(
        api_key=context.mimo_api_key,
        base_url=context.mimo_base_url,
        timeout=max(1, int(context.python_timeout_seconds)),
    )
    completion = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[{"role": "user", "content": content}],
        max_completion_tokens=2048,
        extra_body={"thinking": {"type": "disabled"}},
    )
    answer = completion.choices[0].message.content if completion.choices else ""
    if not answer:
        raise RuntimeError("MiMo returned an empty image analysis")
    return answer
