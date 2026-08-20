# CuteHarness

CuteHarness is a small FastAPI web app for password-protected multi-turn Agent chat. It streams OpenAI-compatible model thinking and final-answer output to the browser, supports tool calls, and can call local tools such as Python execution, application scheduled tasks, and DingTalk messages.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 7998
```

Open `http://127.0.0.1:7998`.

The local `.env` file contains model provider API keys and the app password. Configure `DINGTALK_WEBHOOK_URL` before expecting DingTalk delivery.

## Configuration

- `DEEPSEEK_API_KEY`: DeepSeek API key.
- `DEEPSEEK_BASE_URL`: DeepSeek OpenAI-compatible API base URL. Defaults to `https://api.deepseek.com`.
- `DEEPSEEK_MODEL`: startup/default DeepSeek model fallback. Defaults to `deepseek-v4-flash`.
- `MIMO_API_KEY`: Xiaomi MiMo API key.
- `MIMO_BASE_URL`: Xiaomi MiMo OpenAI-compatible API base URL. Defaults to `https://api.xiaomimimo.com/v1`.
- `APP_PASSWORD`: web login password. The session cookie is valid for 30 days.
- `SECRET_KEY`: session signing key.
- `DINGTALK_WEBHOOK_URL`: DingTalk robot webhook URL.
- `DINGTALK_PUBLIC_BASE_URL`: public HTTPS base URL used for DingTalk markdown file links. Defaults to `https://tenzi.store:7997/`.

## Data

Conversation records are stored as individual JSON files under `data/conversations/`. Writes go through an in-memory cache and are flushed to disk after a short idle window (0.25s) or once pending changes exceed a size threshold (4096 chars), so streaming long replies does not hammer the filesystem. Reads are served from the cache when available.

Application scheduled tasks are stored in `data/scheduled_tasks.json`, UI-editable Agent settings including active model provider/model are stored in `data/settings.json`, and global Agent memories are stored in `data/memories.json`.

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
- `run_bash`: runs `bash -lc <command>` in the workspace with a timeout. Pass `background: true` for long-running commands; it returns immediately with a PID and a log file under `.cuteharness-logs/`.
- `read_file`: reads a workspace text file, optionally restricted to a line range.
- `apply_patch`: applies a structured multi-file patch inside the workspace.
- `edit_file`: edits an existing workspace file by replacing an exact string (unique match or `replace_all`).
- `grep`: searches workspace file contents with a regex; returns matching files, matching lines with line numbers, or per-file counts.
- `send_file`: sends a workspace file to the web page (inline image or download link).
- `view_images`: analyzes up to 8 workspace images with MiMo V2.5. Image paths must come from uploaded files; the tool reads them locally and sends Base64 image data to MiMo.
- `list_scheduled_tasks`: lists CuteHarness application scheduled tasks.
- `create_scheduled_task`: creates an application scheduled task.
- `delete_scheduled_task`: deletes an application scheduled task.
- `add_memory`: adds a key, durable, non-duplicate long-term memory.
- `update_memory`: updates an existing memory by id.
- `delete_memory`: deletes an existing memory by id.
- `ask_user`: sends an interactive question card to the user, optionally with clickable options. The user's answer arrives as the next user message in the conversation.
- `send_dingtalk_message`: sends a DingTalk markdown message and automatically prefixes title and body with `[业务通知]`. It accepts optional `file_paths` workspace-relative paths; image files are appended inline and other files are appended as links using `DINGTALK_PUBLIC_BASE_URL`.

- `list_conversations`: lists recent conversation history.
- `get_conversation`: reads a conversation by id.

Scheduled task schedules support `once`, `interval`, and five-field `cron` expressions. Examples include `30m` for a thirty-minute interval and `0 9 * * 1-5` for 09:00 every weekday. Existing `daily` and `interval_minutes` tasks remain compatible. Schedules use `Asia/Shanghai` by default and executions missed while the application is stopped are skipped rather than replayed after restart.

DingTalk is no longer pushed automatically after every reply. The Agent sends DingTalk messages only when it calls `send_dingtalk_message`.

When the chat input is focused, paste an image with Ctrl+V to add it as an attachment. Pasted images use the same upload path and can be removed from the composer before sending.

## Long Conversations

- **Compression.** When a conversation grows too long, click **压缩对话** in the composer (or `POST /conversations/{id}/compress`). Everything before the compression boundary is collapsed to plain user/assistant text when building the model context - tool calls and results are dropped from what the model sees, while the full history stays on disk. Compressing is refused with 409 while the conversation is running.
- **Tool result previews.** Tool outputs are shown in the chat as a preview limited to the first 4000 characters. Truncated results link to `/conversations/{id}/tool-results/{message_id}` which returns the full output.

## Skills

Reusable agent workflows live as Markdown files under `skills/` (e.g. `skills/讲笑话.md` for the joke-telling flow). The agent reads the relevant skill file before executing a workflow it has not memorized.
