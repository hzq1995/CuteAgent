import json
import os
import time
from pathlib import Path
from typing import Any
from uuid import uuid4


WINDOWS_TRANSIENT_ERRORS = {5, 32}


def write_json_atomic(path: Path, value: Any, *, retries: int = 8, delay: float = 0.025) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())

        for attempt in range(retries + 1):
            try:
                tmp_path.replace(path)
                return
            except OSError as exc:
                if not _is_transient_replace_error(exc) or attempt >= retries:
                    raise
                time.sleep(delay * (2**attempt))
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass


def _is_transient_replace_error(exc: OSError) -> bool:
    return getattr(exc, "winerror", None) in WINDOWS_TRANSIENT_ERRORS
