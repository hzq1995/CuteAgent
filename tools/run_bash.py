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
            "Run a bash command from the CuteHarness project root and return its output and exit status. "
            "Run in the foreground by default. Set background=true only when the command should "
            "keep running after this call returns, such as a dev server or watcher. Keep it false "
            "for tests, builds, installs, scripts, or any command whose output or exit code is needed; "
            "use timeout_seconds for slow commands. Background mode returns immediately with a PID "
            "and log path; check the log with a later run_bash call. It is not a durable service manager. "
            "Do not use nohup, setsid, or disown."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Bash command to execute."},
                "timeout_seconds": {
                    "type": "integer",
                    "description": "Foreground timeout in seconds. Increase this for slow commands when you need their result; ignored when background=true. Default 60.",
                    "default": 60,
                },
                "background": {
                    "type": "boolean",
                    "description": (
                        "Return immediately while the command continues. Use true only for continuous "
                        "processes such as a dev server or watcher. Keep false for tests, builds, installs, "
                        "scripts, or commands whose output/exit code must be checked. If unsure, use false."
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
        ["bash", "-l"],
        cwd=context.base_dir,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        start_new_session=True,
    )
    _send_command_over_stdin(process, command)
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


def _send_command_over_stdin(process: subprocess.Popen, command: str) -> None:
    """Keep command text out of bash argv so tools like `pkill -f` cannot match it."""
    if process.stdin is None:
        raise RuntimeError("bash stdin is unavailable")
    stdin = process.stdin
    try:
        stdin.write(command)
    except BrokenPipeError:
        # A login profile may terminate bash before it starts reading the command.
        pass
    finally:
        stdin.close()
        # communicate() otherwise tries to flush the already-closed stream.
        process.stdin = None


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
            ["bash", "-l"],
            cwd=context.base_dir,
            stdin=subprocess.PIPE,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            start_new_session=True,
        )
        _send_command_over_stdin(process, command)

    return {
        "background": True,
        "pid": process.pid,
        "log_file": str(log_path.relative_to(context.base_dir)),
    }
