from pathlib import Path


MAX_IMAGES = 8
MAX_IMAGE_BYTES = 20 * 1024 * 1024

SUPPORTED_IMAGE_TYPES = {
    ".bmp": "image/bmp",
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def image_mime_type(filename: str) -> str:
    return SUPPORTED_IMAGE_TYPES.get(Path(filename).suffix.lower(), "")
