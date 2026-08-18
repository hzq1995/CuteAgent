import os
import subprocess
import time
from typing import Any
from uuid import uuid4

from app.agent_tools import ToolContext, truncate


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "run_bash",
        "description": (
            "Run a bash command in the CuteHarness workspace and return stdout, stderr, "
            "exit code, and timeout status. Set background=true for a long-running command; "
            "it starts immediately with output written to a log file instead of waiting for it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Bash command to execute."},
                "timeout_seconds": {
                    "type": "integer",
                    "description": "Maximum execution time in seconds before the process is killed. Capped by the global tool timeout setting. Default 60.",
                    "default": 60,
                },
                "background": {
                    "type": "boolean",
                    "description": (
                        "Start the command as a detached background process. Returns its PID and "
                        "workspace-relative log file path; stdout and stderr are written to that log."
                    ),
                    "default": False,
                },
            },
            "required": ["command"],
        },
    },
}


def run(
    context: ToolContext,
    command: str,
    timeout_seconds: int = 60,
    background: bool = False,
) -> dict[str, Any]:
    if background:
        return _start_background_process(context, command)

    timeout = max(1, min(int(timeout_seconds), context.python_timeout_seconds))
    started_at = time.monotonic()
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    process = subprocess.Popen(
        ["bash", "-lc", command],
        cwd=context.base_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
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


def _start_background_process(context: ToolContext, command: str) -> dict[str, Any]:
    """Start a command without leaving it attached to this tool's output pipes."""
    logs_dir = context.base_dir / ".cuteharness-logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"bash-{uuid4().hex}.log"

    with log_path.open("w", encoding="utf-8") as log_file:
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["PYTHONUTF8"] = "1"
        process = subprocess.Popen(
            ["bash", "-lc", command],
            cwd=context.base_dir,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            start_new_session=True,
        )

    return {
        "background": True,
        "pid": process.pid,
        "log_file": str(log_path.relative_to(context.base_dir)),
    }
