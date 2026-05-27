import subprocess
import sys
from typing import Any

from app.agent_tools import ToolContext, truncate


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "run_python",
        "description": "Run Python code on the local machine and return stdout, stderr, exit code, and timeout status. Use print() to get output.",
        "parameters": {
            "type": "object",
            "properties": {"code": {"type": "string", "description": "Python code to execute."}},
            "required": ["code"],
        },
    },
}


def run(context: ToolContext, code: str) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=context.base_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=context.python_timeout_seconds,
        )
        return {
            "stdout": truncate(completed.stdout),
            "stderr": truncate(completed.stderr),
            "exit_code": completed.returncode,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "stdout": truncate(exc.stdout),
            "stderr": truncate(exc.stderr),
            "exit_code": None,
            "timed_out": True,
        }
