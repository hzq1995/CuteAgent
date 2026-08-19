import os
import re
import signal
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
            "it starts immediately with output written to a log file instead of waiting for it. "
            "Background mode is not a durable service supervisor; use an external service "
            "manager for processes that must survive execution-environment cleanup. Do not "
            "use nohup, setsid, or disown for durable services in the Pika execution environment."
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
    if _has_unsupported_detach_command(command):
        raise ValueError(
            "Pika reclaims the complete execution process tree; nohup/setsid/disown cannot "
            "keep a service alive. Use the project's external service manager instead."
        )
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
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        start_new_session=True,
    )
    try:
        while process.poll() is None:
            if context.is_cancelled():
                _terminate_process_tree(process)
                stdout, stderr = _communicate_after_termination(process)
                return {
                    "stdout": truncate(stdout),
                    "stderr": truncate(stderr),
                    "exit_code": process.returncode,
                    "timed_out": False,
                    "cancelled": True,
                }
            if time.monotonic() - started_at >= timeout:
                _terminate_process_tree(process)
                stdout, stderr = _communicate_after_termination(process)
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
            _terminate_process_tree(process)
            _communicate_after_termination(process)
        raise


def _has_unsupported_detach_command(command: str) -> bool:
    return bool(
        re.search(
            r"(?<![\w-])(?:nohup|setsid|disown)(?=\s|$)",
            command,
            flags=re.IGNORECASE,
        )
    )


def _terminate_process_tree(process: subprocess.Popen) -> None:
    """Stop the shell and descendants so they cannot keep tool pipes open."""
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
            return
        except (ProcessLookupError, OSError):
            pass
    elif os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=5,
            )
            return
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass

    try:
        process.kill()
    except ProcessLookupError:
        pass


def _communicate_after_termination(process: subprocess.Popen) -> tuple[str, str]:
    """Collect output after termination without waiting forever on inherited pipes."""
    try:
        return process.communicate(timeout=2)
    except subprocess.TimeoutExpired as exc:
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        return stdout, stderr


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
