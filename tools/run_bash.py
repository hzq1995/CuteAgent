import subprocess
from typing import Any

from app.agent_tools import ToolContext, truncate


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "run_bash",
        "description": "Run a bash command in the CuteHarness workspace and return stdout, stderr, exit code, and timeout status.",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string", "description": "Bash command to execute."}},
            "required": ["command"],
        },
    },
}


def run(context: ToolContext, command: str) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            ["bash", "-lc", command],
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
