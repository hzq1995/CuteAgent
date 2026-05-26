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

Application scheduled tasks are stored in `data/scheduled_tasks.json`, and UI-editable Agent settings are stored in `data/settings.json`.

## Agent Tools

- `run_python`: runs local Python code with a timeout.
- `list_scheduled_tasks`: lists CuteHarness application scheduled tasks.
- `create_scheduled_task`: creates an application scheduled task.
- `delete_scheduled_task`: deletes an application scheduled task.
- `send_dingtalk_message`: sends a DingTalk markdown message and automatically prefixes title and body with `[业务通知]`.

DingTalk is no longer pushed automatically after every reply. The Agent sends DingTalk messages only when it calls `send_dingtalk_message`.
