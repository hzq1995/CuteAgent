import json
import mimetypes
import os
import re
import shutil
from pathlib import Path
from urllib.parse import quote, urlencode
from uuid import uuid4

import requests


DEFAULT_DINGTALK_SEND_URL = "https://oapi.dingtalk.com/robot/send"
DEFAULT_DINGTALK_PUBLIC_BASE_URL = "https://tenzi.store:7997/"


class DingdingRobot:
    def __init__(self, webhook_url=None, access_token=None):
        self.url = (
            webhook_url
            or self._url_from_token(access_token)
            or os.getenv("DINGTALK_WEBHOOK_URL")
            or self._url_from_token(os.getenv("DINGTALK_ACCESS_TOKEN"))
        )
        self.headers = {"Content-Type": "application/json"}

    @staticmethod
    def _url_from_token(access_token):
        if not access_token:
            return ""
        return f"{DEFAULT_DINGTALK_SEND_URL}?{urlencode({'access_token': access_token})}"

    def send_markdown(
        self,
        title,
        text,
        at_mobiles=None,
        at_user_ids=None,
        is_at_all=False,
        file_paths=None,
        base_dir=None,
        public_base_url=None,
    ):
        if not self.url:
            return {"error": "DingTalk webhook is not configured"}
        text = text or ""
        file_paths = file_paths or []
        if file_paths:
            publish_result = publish_public_files(
                base_dir=base_dir,
                file_paths=file_paths,
                public_base_url=public_base_url,
            )
            if "error" in publish_result:
                return publish_result
            text = append_public_files_to_markdown(text, publish_result["files"])
        data = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": text,
            },
            "at": {
                "atMobiles": at_mobiles or [],
                "atUserIds": at_user_ids or [],
                "isAtAll": is_at_all,
            },
        }

        try:
            response = requests.post(self.url, headers=self.headers, data=json.dumps(data), timeout=20)
            return response.json()
        except Exception as e:
            return {"error": str(e)}

    def send_text(self, content, at_mobiles=None, at_user_ids=None, is_at_all=False):
        if not self.url:
            return {"error": "DingTalk webhook is not configured"}
        data = {
            "msgtype": "text",
            "text": {
                "content": content,
            },
            "at": {
                "atMobiles": at_mobiles or [],
                "atUserIds": at_user_ids or [],
                "isAtAll": is_at_all,
            },
        }

        try:
            response = requests.post(self.url, headers=self.headers, data=json.dumps(data), timeout=20)
            return response.json()
        except Exception as e:
            return {"error": str(e)}


def publish_public_files(base_dir, file_paths, public_base_url=None):
    if base_dir is None:
        return {"error": "base_dir is required when sending DingTalk markdown files"}

    public_base_url = normalize_public_base_url(public_base_url)
    if not public_base_url:
        return {"error": "DingTalk public base URL is not configured"}
    if not public_base_url.startswith("https://"):
        return {"error": "DingTalk public base URL must start with https://"}

    try:
        base = Path(base_dir).resolve()
        published = []
        for raw_path in file_paths:
            source = resolve_workspace_file(base, raw_path)
            file_id = uuid4().hex
            filename = safe_filename(source.name)
            target_dir = base / "data" / "public_files" / file_id
            target_dir.mkdir(parents=True, exist_ok=False)
            target = target_dir / filename
            shutil.copy2(source, target)

            mime_type, _ = mimetypes.guess_type(filename)
            if not mime_type:
                mime_type, _ = mimetypes.guess_type(source.name)
            mime_type = mime_type or "application/octet-stream"
            published.append(
                {
                    "id": file_id,
                    "name": filename,
                    "url": f"{public_base_url}/public-files/{file_id}/{quote(filename)}",
                    "mime_type": mime_type,
                    "is_image": mime_type.startswith("image/"),
                }
            )
        return {"files": published}
    except Exception as exc:
        return {"error": str(exc)}


def append_public_files_to_markdown(text, files):
    additions = []
    for file in files:
        name = file["name"]
        url = file["url"]
        if file["is_image"]:
            additions.append(f"![{name}]({url})")
        else:
            additions.append(f"[{name}]({url})")
    if not additions:
        return text
    return f"{text.rstrip()}\n\n" + "\n\n".join(additions)


def normalize_public_base_url(public_base_url=None):
    value = public_base_url or os.getenv("DINGTALK_PUBLIC_BASE_URL") or DEFAULT_DINGTALK_PUBLIC_BASE_URL
    return value.strip().rstrip("/") if value else ""


def resolve_workspace_file(base_dir, raw_path):
    if not raw_path or not str(raw_path).strip():
        raise ValueError("file path is required")

    candidate = Path(str(raw_path)).expanduser()
    if candidate.is_absolute():
        raise ValueError("file path must be relative to the CuteHarness workspace; absolute paths are not allowed")
    resolved = (base_dir / candidate).resolve()

    if resolved != base_dir and base_dir not in resolved.parents:
        raise ValueError("Only files inside the CuteHarness workspace can be sent")
    if not resolved.exists():
        raise FileNotFoundError(f"File not found: {raw_path}")
    if not resolved.is_file():
        raise ValueError(f"Path is not a file: {raw_path}")
    return resolved


def safe_filename(value):
    name = Path(value or "file").name.strip().replace("\\", "_").replace("/", "_")
    name = re.sub(r"[\x00-\x1f\x7f]+", "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        return "file"
    return name[:160]


if __name__ == "__main__":
    robot = DingdingRobot()
    result = robot.send_text("CuteHarness DingTalk test")
    print("Send result:", result)
