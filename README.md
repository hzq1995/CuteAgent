# CuteHarness

CuteHarness is a small FastAPI web app that accepts a password-protected prompt, streams DeepSeek V4 Flash thinking and final-answer output to the browser, and sends the final answer to DingTalk.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`.

The local `.env` file contains the DeepSeek API key and a generated app password. Configure `DINGTALK_WEBHOOK_URL` before expecting DingTalk delivery.

## Configuration

- `DEEPSEEK_API_KEY`: DeepSeek API key.
- `APP_PASSWORD`: web login password. The session cookie is valid for 30 days.
- `SECRET_KEY`: session signing key.
- `DINGTALK_WEBHOOK_URL`: DingTalk robot webhook URL.

## Data

Task records are stored in `data/tasks.json`.
