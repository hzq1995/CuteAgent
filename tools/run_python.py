import subprocess
import sys
import time
from typing import Any

from app.agent_tools import ToolContext, truncate


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "run_python",
        "description": "Run Python code on the local machine and return stdout, stderr, exit code, and timeout status. Use print() to get output.",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python code to execute."},
                "timeout_seconds": {
                    "type": "integer",
                    "description": "Maximum execution time in seconds before the process is killed. Capped by the global tool timeout setting. Default 60.",
                    "default": 60,
                },
            },
            "required": ["code"],
        },
    },
}


def run(context: ToolContext, code: str, timeout_seconds: int = 60) -> dict[str, Any]:
    timeout = max(1, min(int(timeout_seconds), context.python_timeout_seconds))
    started_at = time.monotonic()
    process = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=context.base_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        while process.poll() is None:
            if context.is_cancelled():
                process.kill()
                stdout, stderr = process.communicate()
                return {
                    "stdout": truncate(stdout),
                    "stderr": truncate(stderr),
                    "exit_code": process.returncode,
                    "timed_out": False,
                    "cancelled": True,
                }
            if time.monotonic() - started_at >= timeout:
                process.kill()
                stdout, stderr = process.communicate()
                return {
                    "stdout": truncate(stdout),
                    "stderr": truncate(stderr),
                    "exit_code": None,
                    "timed_out": True,
                }
            time.sleep(0.1)

        stdout, stderr = process.communicate()
        return {
            "stdout": truncate(stdout),
            "stderr": truncate(stderr),
            "exit_code": process.returncode,
            "timed_out": False,
        }
    except Exception:
        if process.poll() is None:
            process.kill()
            process.communicate()
        raise
