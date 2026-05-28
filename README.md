# CuteHarness

CuteHarness is a small FastAPI web app for password-protected multi-turn Agent chat. It streams DeepSeek V4 Flash thinking and final-answer output to the browser, supports DeepSeek tool calls, and can call local tools such as Python execution, application scheduled tasks, and DingTalk messages.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`.

The local `.env` file contains the DeepSeek API key and app password. Configure `DINGTALK_WEBHOOK_URL` before expecting DingTalk delivery.

## Configuration

- `DEEPSEEK_API_KEY`: DeepSeek API key.
- `APP_PASSWORD`: web login password. The session cookie is valid for 30 days.
- `SECRET_KEY`: session signing key.
- `DINGTALK_WEBHOOK_URL`: DingTalk robot webhook URL.

## Data

Conversation records are stored as individual JSON files under `data/conversations/`.

Application scheduled tasks are stored in `data/scheduled_tasks.json`, UI-editable Agent settings are stored in `data/settings.json`, and global Agent memories are stored in `data/memories.json`.

## Agent Tools

Agent tools are hot-loaded from Python modules under `tools/`. CuteHarness scans the directory once at the start of each agent turn, then uses that same tool registry for all tool calls in that turn. Changes to tool files take effect on the next user message or scheduled task run.

Tool switches are stored in `data/tool_settings.json`:

```json
{
  "disabled_tools": ["run_bash"]
}
```

Disabled tools are not sent to the model and cannot be executed by the local tool runner. New tools are enabled by default unless their names are listed in `disabled_tools`. The settings page can edit this file, and it is intentionally simple enough to change by hand or by an Agent when the user explicitly asks it to modify local files.

Each `tools/*.py` module must export:

- `TOOL_DEFINITION`: a function tool schema with a unique `function.name`.
- `run(context, **kwargs)`: the implementation called with parsed tool arguments.

See `skills/工具创建.md` for the tool creation workflow.

Built-in tools:

- `run_python`: runs local Python code with a timeout.
- `run_bash`: runs `bash -lc <command>` in the workspace with a timeout.
- `list_scheduled_tasks`: lists CuteHarness application scheduled tasks.
- `create_scheduled_task`: creates an application scheduled task.
- `delete_scheduled_task`: deletes an application scheduled task.
- `add_memory`: adds a key, durable, non-duplicate long-term memory.
- `update_memory`: updates an existing memory by id.
- `delete_memory`: deletes an existing memory by id.
- `send_dingtalk_message`: sends a DingTalk markdown message and automatically prefixes title and body with `[业务通知]`.

- `list_conversations`: lists recent conversation history.
- `get_conversation`: reads a conversation by id.

DingTalk is no longer pushed automatically after every reply. The Agent sends DingTalk messages only when it calls `send_dingtalk_message`.

## Skills

See  for the joke-telling workflow.
