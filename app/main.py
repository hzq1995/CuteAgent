import asyncio
import json
import re
import threading
import time
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.agent_tools import AgentToolRunner, load_tools, parse_tool_arguments
from app.app_settings import AppSettingsStore
from app.config import BASE_DIR, get_settings
from app.llm_client import OpenAICompatibleClient
from app.llm_config import MIMO_PROVIDER, custom_model_by_provider, provider_options, request_options_for_provider
from app.memory_store import MemoryStore
from app.image_support import MAX_IMAGE_BYTES, MAX_IMAGES, image_mime_type
from app.schedule_types import DEFAULT_TIMEZONE, ScheduleValidationError
from app.scheduler_store import ScheduledTaskStore
from app.storage import TaskStore
from app.tool_settings import ToolSettingsStore


settings = get_settings()
app = FastAPI(title="CuteHarness")
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    max_age=60 * 60 * 24 * 30,
    same_site="lax",
    https_only=False,
)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
    is_https = request.url.scheme == "https" or forwarded_proto == "https"
    if is_https:
        response.headers.setdefault(
            "Content-Security-Policy",
            "upgrade-insecure-requests; block-all-mixed-content",
        )
        response.headers.setdefault("Strict-Transport-Security", "max-age=0")
    return response


def _tojson_unicode(value, indent=None):
    """tojson filter that keeps non-ASCII chars (e.g. Chinese) readable."""
    from markupsafe import Markup
    result = json.dumps(value, ensure_ascii=False, indent=indent)
    # Escape HTML special chars to stay safe in HTML context
    result = result.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return Markup(result)


templates.env.filters["tojson"] = _tojson_unicode
templates.env.globals["static_v"] = str(int(time.time()))

store = TaskStore(BASE_DIR / "data" / "conversations")
scheduled_task_store = ScheduledTaskStore(BASE_DIR / "data" / "scheduled_tasks.json")
app_settings_store = AppSettingsStore(BASE_DIR / "data" / "settings.json")
tool_settings_store = ToolSettingsStore(BASE_DIR / "data" / "tool_settings.json")
memory_store = MemoryStore(BASE_DIR / "data" / "memories.json")
SKILLS_DIR = BASE_DIR / "skills"
CANCELLED_STATUS = "cancelled"
SCHEDULED_TASK_PROMPT_PREFIX = (
    "[定时任务触发]\n"
    "这是一条由 CuteHarness 定时任务自动触发的消息。"
    "请按照定时任务内容执行：\n"
)


class ConversationCancelled(Exception):
    pass


class ConversationRunRegistry:
    def __init__(self):
        self.lock = threading.Lock()
        self.events: dict[tuple[str, str], threading.Event] = {}

    def start(self, conversation_id: str, message_id: str) -> threading.Event:
        event = threading.Event()
        with self.lock:
            self.events[(conversation_id, message_id)] = event
        return event

    def cancel(self, conversation_id: str, message_id: str | None = None) -> bool:
        cancelled = False
        with self.lock:
            for (current_conversation_id, current_message_id), event in list(self.events.items()):
                if current_conversation_id != conversation_id:
                    continue
                if message_id and current_message_id != message_id:
                    continue
                event.set()
                cancelled = True
        return cancelled

    def finish(self, conversation_id: str, message_id: str, event: threading.Event) -> None:
        with self.lock:
            key = (conversation_id, message_id)
            if self.events.get(key) is event:
                self.events.pop(key, None)


running_conversations = ConversationRunRegistry()


def require_login(request: Request) -> None:
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)


def redirect_if_unauthenticated(request: Request) -> RedirectResponse | None:
    if not request.session.get("authenticated"):
        return RedirectResponse("/login", status_code=303)
    return None


@app.exception_handler(401)
async def auth_exception_handler(request: Request, exc: HTTPException) -> RedirectResponse:
    return RedirectResponse("/login", status_code=303)


@app.on_event("startup")
async def start_scheduler() -> None:
    scheduled_task_store.mark_interrupted_runs()
    scheduled_task_store.skip_missed_tasks()
    app.state.scheduler_task = asyncio.create_task(scheduler_loop())


@app.on_event("shutdown")
async def stop_scheduler() -> None:
    task = getattr(app.state, "scheduler_task", None)
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if request.session.get("authenticated"):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": ""})


@app.post("/login")
async def login(request: Request, password: str = Form(...)):
    if password != settings.app_password:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "密码不正确"},
            status_code=400,
        )
    request.session["authenticated"] = True
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    redirect = redirect_if_unauthenticated(request)
    if redirect:
        return redirect
    return render_chat(request, conversation=None)


@app.post("/conversations")
async def create_conversation(
    request: Request,
    background_tasks: BackgroundTasks,
    prompt: str = Form(""),
):
    require_login(request)
    cleaned_prompt = prompt.strip()
    uploads = await uploaded_files_from_request(request)
    if not cleaned_prompt and not uploads:
        if wants_json_response(request):
            return JSONResponse({"error": "Prompt is required"}, status_code=400)
        return RedirectResponse("/", status_code=303)

    conversation = store.create_conversation(cleaned_prompt)
    user = conversation["messages"][0]
    attachments = await save_uploaded_files(conversation["id"], user["id"], uploads)
    if attachments:
        user = store.update_message(
            conversation["id"],
            user["id"],
            content=compose_prompt_with_attachments(cleaned_prompt, attachments),
            attachments=attachments,
        )
    assistant = conversation["messages"][-1]
    background_tasks.add_task(run_conversation_turn, conversation["id"], assistant["id"])
    if wants_json_response(request):
        return JSONResponse(submit_payload(conversation["id"], user, assistant), status_code=201)
    return RedirectResponse(f"/conversations/{conversation['id']}", status_code=303)


@app.get("/conversations/{conversation_id}", response_class=HTMLResponse)
async def conversation_detail(conversation_id: str, request: Request):
    require_login(request)
    conversation = store.get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return render_chat(request, conversation=conversation)


@app.post("/conversations/{conversation_id}/messages")
async def append_message(
    conversation_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    prompt: str = Form(""),
):
    require_login(request)
    cleaned_prompt = prompt.strip()
    uploads = await uploaded_files_from_request(request)
    if not cleaned_prompt and not uploads:
        if wants_json_response(request):
            return JSONResponse({"error": "Prompt is required"}, status_code=400)
        return RedirectResponse(f"/conversations/{conversation_id}", status_code=303)

    conversation = store.get_conversation(conversation_id)
    if not conversation:
        if wants_json_response(request):
            return JSONResponse({"error": "Conversation not found"}, status_code=404)
        raise HTTPException(status_code=404, detail="Conversation not found")
    if store.has_running_message(conversation_id):
        if wants_json_response(request):
            return JSONResponse({"error": "Conversation already has a running message"}, status_code=409)
        return RedirectResponse(f"/conversations/{conversation_id}", status_code=303)

    user = store.append_user_message(conversation_id, cleaned_prompt)
    attachments = await save_uploaded_files(conversation_id, user["id"], uploads)
    if attachments:
        user = store.update_message(
            conversation_id,
            user["id"],
            content=compose_prompt_with_attachments(cleaned_prompt, attachments),
            attachments=attachments,
        )
    assistant = store.create_assistant_message(conversation_id)
    background_tasks.add_task(run_conversation_turn, conversation_id, assistant["id"])
    if wants_json_response(request):
        return JSONResponse(submit_payload(conversation_id, user, assistant))
    return RedirectResponse(f"/conversations/{conversation_id}", status_code=303)


@app.post("/conversations/{conversation_id}/cancel")
async def cancel_conversation(conversation_id: str, request: Request):
    require_login(request)
    conversation = store.get_conversation(conversation_id)
    if not conversation:
        return JSONResponse({"error": "Conversation not found"}, status_code=404)

    assistant = latest_assistant(conversation)
    if not assistant or assistant.get("status") not in {"queued", "running"}:
        return JSONResponse({"error": "Conversation has no running message"}, status_code=409)

    running_conversations.cancel(conversation_id, assistant["id"])
    store.update_message(conversation_id, assistant["id"], status=CANCELLED_STATUS)
    store.update_conversation(conversation_id, status=CANCELLED_STATUS, error="")
    return JSONResponse(
        {
            "conversation_id": conversation_id,
            "message_id": assistant["id"],
            "status": CANCELLED_STATUS,
        }
    )


@app.get("/conversations/{conversation_id}/stream")
async def conversation_stream(
    conversation_id: str,
    request: Request,
    _: None = Depends(require_login),
):
    async def events():
        assistant_id = ""
        sent_reasoning_len = int(request.query_params.get("reasoning_offset", "0") or "0")
        sent_answer_len = int(request.query_params.get("answer_offset", "0") or "0")
        sent_tool_count = int(request.query_params.get("tool_count", "0") or "0")
        sent_tool_call_count = 0
        sent_status = ""
        sent_context_tokens: int | None = None

        while True:
            if await request.is_disconnected():
                break

            conversation = store.get_conversation(conversation_id)
            if not conversation:
                yield sse("error", {"error": "Conversation not found"})
                break

            assistant = latest_assistant(conversation)
            if not assistant:
                yield sse("done", {"status": conversation["status"]})
                break

            if assistant["id"] != assistant_id:
                if assistant_id:
                    sent_reasoning_len = 0
                    sent_answer_len = 0
                    sent_tool_count = 0
                    sent_tool_call_count = 0
                assistant_id = assistant["id"]
                sent_status = ""
                yield sse("assistant", {"message_id": assistant_id})

            if assistant["status"] != sent_status:
                sent_status = assistant["status"]
                yield sse(
                    "status",
                    {
                        "conversation_status": conversation["status"],
                        "message_id": assistant_id,
                        "status": sent_status,
                    },
                )

            reasoning = assistant.get("reasoning_content") or ""
            if len(reasoning) > sent_reasoning_len:
                yield sse(
                    "reasoning",
                    {"message_id": assistant_id, "delta": reasoning[sent_reasoning_len:]},
                )
                sent_reasoning_len = len(reasoning)

            answer = assistant.get("content") or ""
            if len(answer) > sent_answer_len:
                yield sse(
                    "answer",
                    {"message_id": assistant_id, "delta": answer[sent_answer_len:]},
                )
                sent_answer_len = len(answer)

            tool_calls = assistant.get("tool_calls") or []
            if len(tool_calls) > sent_tool_call_count:
                for tool_call in tool_calls[sent_tool_call_count:]:
                    function = tool_call.get("function", {})
                    raw_arguments = function.get("arguments", "") or "{}"
                    try:
                        display_arguments = parse_tool_arguments(raw_arguments)
                    except (TypeError, ValueError):
                        display_arguments = raw_arguments
                    yield sse(
                        "tool_call",
                        {
                            "message_id": assistant_id,
                            "message": {
                                "tool_call_id": tool_call.get("id", ""),
                                "name": function.get("name", ""),
                                "arguments": display_arguments,
                            },
                        },
                    )
                sent_tool_call_count = len(tool_calls)

            tools = current_tool_messages(conversation, assistant_id)
            if len(tools) > sent_tool_count:
                for tool_message in tools[sent_tool_count:]:
                    yield sse("tool_call_result", {"message_id": assistant_id, "message": tool_message})
                sent_tool_count = len(tools)

            context_tokens = current_context_token_estimate(conversation_id)
            if context_tokens != sent_context_tokens:
                sent_context_tokens = context_tokens
                yield sse(
                    "context_tokens",
                    {"message_id": assistant_id, "estimated_tokens": context_tokens},
                )

            if conversation.get("error"):
                yield sse("error", {"message_id": assistant_id, "error": conversation["error"]})
                break

            if assistant["status"] == "failed":
                yield sse("done", {"status": "failed", "message_id": assistant_id})
                break
            if assistant["status"] == CANCELLED_STATUS:
                yield sse("done", {"status": CANCELLED_STATUS, "message_id": assistant_id})
                break
            if assistant["status"] == "succeeded":
                yield sse("done", {"status": "succeeded", "message_id": assistant_id})
                break

            await asyncio.sleep(0.7)

    return StreamingResponse(events(), media_type="text/event-stream")


@app.get("/files/{file_id}/{filename}")
async def shared_file(file_id: str, filename: str, _: None = Depends(require_login)):
    if not is_safe_file_id(file_id) or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=404, detail="File not found")

    shared_root = (BASE_DIR / "data" / "shared_files").resolve()
    path = (shared_root / file_id / filename).resolve()
    if shared_root != path and shared_root not in path.parents:
        raise HTTPException(status_code=404, detail="File not found")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    media_type = guess_media_type(path.name)
    disposition = "inline" if media_type.startswith("image/") else "attachment"
    return FileResponse(
        path,
        media_type=media_type,
        filename=path.name,
        content_disposition_type=disposition,
    )


@app.get("/uploads/{conversation_id}/{message_id}/{filename}")
async def uploaded_file(conversation_id: str, message_id: str, filename: str, _: None = Depends(require_login)):
    if not is_safe_upload_id(conversation_id) or not is_safe_upload_id(message_id):
        raise HTTPException(status_code=404, detail="File not found")
    if "/" in filename or "\\" in filename:
        raise HTTPException(status_code=404, detail="File not found")

    upload_root = (BASE_DIR / "data" / "uploads").resolve()
    path = (upload_root / conversation_id / message_id / filename).resolve()
    if upload_root != path and upload_root not in path.parents:
        raise HTTPException(status_code=404, detail="File not found")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    media_type = guess_media_type(path.name)
    disposition = "inline" if media_type.startswith("image/") else "attachment"
    return FileResponse(
        path,
        media_type=media_type,
        filename=path.name,
        content_disposition_type=disposition,
    )


@app.get("/public-files/{file_id}/{filename}")
async def public_file(file_id: str, filename: str):
    if not is_safe_file_id(file_id) or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=404, detail="File not found")

    public_root = (BASE_DIR / "data" / "public_files").resolve()
    path = (public_root / file_id / filename).resolve()
    if public_root != path and public_root not in path.parents:
        raise HTTPException(status_code=404, detail="File not found")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    media_type = guess_media_type(path.name)
    disposition = "inline" if media_type.startswith("image/") else "attachment"
    return FileResponse(
        path,
        media_type=media_type,
        filename=path.name,
        content_disposition_type=disposition,
    )


@app.get("/scheduled-tasks", response_class=HTMLResponse)
async def scheduled_tasks_page(request: Request):
    redirect = redirect_if_unauthenticated(request)
    if redirect:
        return redirect
    return render_scheduled_tasks(request)


def scheduled_task_form_data(task: dict | None = None) -> dict:
    if task:
        schedule_type = task.get("schedule_type", "cron")
        schedule_value = task.get("schedule_value", "")
        if schedule_type == "once":
            try:
                schedule_value = datetime.fromisoformat(task.get("schedule_config", {}).get("at", "")).strftime(
                    "%Y-%m-%dT%H:%M"
                )
            except (TypeError, ValueError):
                schedule_value = task.get("schedule_value", "")
        return {
            "title": task.get("title", ""),
            "prompt": task.get("prompt", ""),
            "schedule_type": schedule_type,
            "schedule_value": schedule_value,
            "timezone": task.get("timezone", DEFAULT_TIMEZONE),
            "enabled": bool(task.get("enabled", True)),
            "auto_delete": bool(task.get("auto_delete", True)),
        }
    return {
        "title": "",
        "prompt": "",
        "schedule_type": "cron",
        "schedule_value": "0 9 * * *",
        "timezone": DEFAULT_TIMEZONE,
        "enabled": True,
        "auto_delete": True,
    }


def render_scheduled_tasks(
    request: Request,
    *,
    editing_task: dict | None = None,
    form_data: dict | None = None,
    form_error: str = "",
    status_code: int = 200,
):
    return templates.TemplateResponse(
        "scheduled_tasks.html",
        base_context(request, active_page="scheduled_tasks")
        | {
            "scheduled_tasks": scheduled_task_store.list_tasks(),
            "editing_task": editing_task,
            "form_data": form_data or scheduled_task_form_data(editing_task),
            "form_error": form_error,
        },
        status_code=status_code,
    )


@app.post("/scheduled-tasks")
async def create_scheduled_task(
    request: Request,
    title: str = Form(""),
    prompt: str = Form(...),
    schedule_type: str = Form(...),
    schedule_value: str = Form(...),
    timezone: str = Form(DEFAULT_TIMEZONE),
    enabled: str | None = Form(None),
    auto_delete: str | None = Form(None),
):
    require_login(request)
    form_data = {
        "title": title,
        "prompt": prompt,
        "schedule_type": schedule_type,
        "schedule_value": schedule_value,
        "timezone": timezone,
        "enabled": enabled == "on",
        "auto_delete": auto_delete == "on",
    }
    try:
        scheduled_task_store.create_task(
            title,
            prompt,
            schedule_type,
            schedule_value,
            enabled == "on",
            auto_delete == "on",
            timezone,
        )
    except ScheduleValidationError as exc:
        return render_scheduled_tasks(request, form_data=form_data, form_error=str(exc), status_code=400)
    return RedirectResponse("/scheduled-tasks", status_code=303)


@app.get("/scheduled-tasks/{task_id}", response_class=HTMLResponse)
async def edit_scheduled_task_page(task_id: str, request: Request):
    require_login(request)
    task = scheduled_task_store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Scheduled task not found")
    return render_scheduled_tasks(request, editing_task=task)


@app.post("/scheduled-tasks/{task_id}")
async def update_scheduled_task(
    task_id: str,
    request: Request,
    title: str = Form(""),
    prompt: str = Form(...),
    schedule_type: str = Form(...),
    schedule_value: str = Form(...),
    timezone: str = Form(DEFAULT_TIMEZONE),
    enabled: str | None = Form(None),
    auto_delete: str | None = Form(None),
):
    require_login(request)
    task = scheduled_task_store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Scheduled task not found")
    form_data = {
        "title": title,
        "prompt": prompt,
        "schedule_type": schedule_type,
        "schedule_value": schedule_value,
        "timezone": timezone,
        "enabled": enabled == "on",
        "auto_delete": auto_delete == "on",
    }
    try:
        scheduled_task_store.update_task(
            task_id,
            title,
            prompt,
            schedule_type,
            schedule_value,
            enabled == "on",
            auto_delete == "on",
            timezone,
        )
    except ScheduleValidationError as exc:
        return render_scheduled_tasks(request, editing_task=task, form_data=form_data, form_error=str(exc), status_code=400)
    return RedirectResponse("/scheduled-tasks", status_code=303)


@app.post("/scheduled-tasks/{task_id}/delete")
async def delete_scheduled_task(task_id: str, request: Request):
    require_login(request)
    scheduled_task_store.delete_task(task_id)
    return RedirectResponse("/scheduled-tasks", status_code=303)


@app.get("/skills", response_class=HTMLResponse)
async def skills_page(request: Request):
    redirect = redirect_if_unauthenticated(request)
    if redirect:
        return redirect
    skills = list_skill_files()
    selected_name = skills[0]["name"] if skills else ""
    selected = read_skill(selected_name) if selected_name else None
    return templates.TemplateResponse(
        "skills.html",
        base_context(request, active_page="skills")
        | skills_context(skills=skills, selected=selected, saved=False),
    )


@app.get("/skills/{filename}", response_class=HTMLResponse)
async def skill_detail_page(filename: str, request: Request):
    redirect = redirect_if_unauthenticated(request)
    if redirect:
        return redirect
    skills = list_skill_files()
    selected = read_skill(filename)
    return templates.TemplateResponse(
        "skills.html",
        base_context(request, active_page="skills")
        | skills_context(skills=skills, selected=selected, saved=False),
    )


@app.post("/skills/{filename}", response_class=HTMLResponse)
async def update_skill(
    filename: str,
    request: Request,
    content: str = Form(""),
    skill_name: str = Form(""),
):
    require_login(request)
    path = resolve_skill_path(filename)
    try:
        target_name = normalize_skill_filename(skill_name or filename)
        target_path = resolve_skill_target_path(target_name)
    except ValueError as exc:
        skills = list_skill_files()
        selected = read_skill(filename)
        return templates.TemplateResponse(
            "skills.html",
            base_context(request, active_page="skills")
            | skills_context(skills=skills, selected=selected, saved=False, error=str(exc)),
            status_code=400,
        )
    if target_path != path and target_path.exists():
        skills = list_skill_files()
        selected = read_skill(filename)
        return templates.TemplateResponse(
            "skills.html",
            base_context(request, active_page="skills")
            | skills_context(skills=skills, selected=selected, saved=False, error="Skill name already exists."),
            status_code=400,
        )
    if target_path != path:
        path.rename(target_path)
        path = target_path
    path.write_text(normalize_skill_content(content), encoding="utf-8", newline="\n")
    skills = list_skill_files()
    selected = read_skill(path.name)
    return templates.TemplateResponse(
        "skills.html",
        base_context(request, active_page="skills")
        | skills_context(skills=skills, selected=selected, saved=True),
    )


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    redirect = redirect_if_unauthenticated(request)
    if redirect:
        return redirect
    return templates.TemplateResponse(
        "settings.html",
        base_context(request, active_page="settings")
        | settings_context(saved=False),
    )


@app.post("/settings", response_class=HTMLResponse)
async def update_settings(
    request: Request,
    llm_provider: str = Form("deepseek"),
    llm_model: str = Form("deepseek-v4-flash"),
    system_prompt: str = Form(""),
    python_timeout_seconds: int = Form(30),
    max_tool_rounds: int = Form(5),
):
    require_login(request)
    form = await request.form()
    enabled_tools = form.getlist("enabled_tools")
    values = app_settings_store.update(llm_provider, llm_model, system_prompt, python_timeout_seconds, max_tool_rounds)
    registry = load_tools()
    tool_settings_store.update_enabled_tools(list(registry.tools.keys()), enabled_tools)
    return templates.TemplateResponse(
        "settings.html",
        base_context(request, active_page="settings")
        | settings_context(app_settings=values, registry=registry, saved=True),
    )


@app.post("/settings/custom-models", response_class=HTMLResponse)
async def add_custom_model(
    request: Request,
    custom_model_name: str = Form(""),
    custom_model_base_url: str = Form(""),
    custom_model_model: str = Form(""),
    custom_model_api_key: str = Form(""),
):
    require_login(request)
    try:
        values = app_settings_store.add_custom_model(
            custom_model_name,
            custom_model_base_url,
            custom_model_model,
            custom_model_api_key,
        )
    except ValueError as exc:
        return templates.TemplateResponse(
            "settings.html",
            base_context(request, active_page="settings")
            | settings_context(app_settings=app_settings_store.get(), saved=False, error=str(exc)),
            status_code=400,
        )
    return templates.TemplateResponse(
        "settings.html",
        base_context(request, active_page="settings")
        | settings_context(app_settings=values, saved=True),
    )


@app.post("/settings/custom-models/{model_id}/delete")
async def delete_custom_model(model_id: str, request: Request):
    require_login(request)
    app_settings_store.delete_custom_model(model_id)
    return RedirectResponse("/settings", status_code=303)


@app.post("/memories/{memory_id}")
async def update_memory(memory_id: str, request: Request, content: str = Form("")):
    require_login(request)
    try:
        memory_store.update_memory(memory_id, content)
    except KeyError:
        raise HTTPException(status_code=404, detail="Memory not found")
    return RedirectResponse("/settings", status_code=303)


@app.post("/memories/{memory_id}/delete")
async def delete_memory(memory_id: str, request: Request):
    require_login(request)
    memory_store.delete_memory(memory_id)
    return RedirectResponse("/settings", status_code=303)


@app.post("/tasks")
async def create_task_compat(
    request: Request,
    background_tasks: BackgroundTasks,
    prompt: str = Form(...),
):
    return await create_conversation(request, background_tasks, prompt)


@app.get("/tasks/{task_id}", response_class=HTMLResponse)
async def task_detail_compat(task_id: str, request: Request):
    return await conversation_detail(task_id, request)


@app.get("/tasks/{task_id}/stream")
async def task_stream_compat(task_id: str, request: Request, _: None = Depends(require_login)):
    return await conversation_stream(task_id, request)


@app.post("/tasks/{task_id}/cancel")
async def task_cancel_compat(task_id: str, request: Request):
    return await cancel_conversation(task_id, request)


def render_chat(request: Request, conversation: dict | None):
    active_assistant = latest_assistant(conversation) if conversation else None
    reasoning_offset = len(active_assistant.get("reasoning_content", "")) if active_assistant else 0
    answer_offset = len(active_assistant.get("content", "")) if active_assistant else 0
    tool_count = len(current_tool_messages(conversation, active_assistant["id"])) if conversation and active_assistant else 0
    return templates.TemplateResponse(
        "index.html",
        base_context(request, active_page="chat")
        | {
            "conversation": conversation,
            "is_running": bool(conversation and store.has_running_message(conversation["id"])),
            "active_assistant": active_assistant,
            "reasoning_offset": reasoning_offset,
            "answer_offset": answer_offset,
            "tool_count": tool_count,
            "context_token_estimate": (
                current_context_token_estimate(conversation["id"]) if conversation else 0
            ),
        },
    )


def base_context(request: Request, active_page: str) -> dict:
    return {
        "request": request,
        "conversations": store.list_conversations(),
        "active_page": active_page,
    }


def settings_context(
    *,
    app_settings: dict | None = None,
    registry=None,
    saved: bool,
    error: str = "",
) -> dict:
    app_settings = app_settings or app_settings_store.get()
    registry = registry or load_tools()
    disabled_tools = tool_settings_store.disabled_tools()
    tools = []
    for name, tool in registry.tools.items():
        try:
            path = str(tool.path.relative_to(BASE_DIR))
        except ValueError:
            path = str(tool.path)
        tools.append(
            {
                "name": name,
                "description": tool.definition.get("function", {}).get("description", ""),
                "path": path,
                "enabled": name not in disabled_tools,
            }
        )
    return {
        "app_settings": app_settings,
        "llm_providers": provider_options(app_settings.get("custom_models", [])),
        "tool_settings": {"disabled_tools": sorted(disabled_tools)},
        "tools": tools,
        "memories": memory_store.list_memories(),
        "saved": saved,
        "error": error,
    }


def skills_context(
    *,
    skills: list[dict],
    selected: dict | None,
    saved: bool,
    error: str = "",
) -> dict:
    return {
        "skills": skills,
        "selected_skill": selected,
        "saved": saved,
        "error": error,
    }


def list_skill_files() -> list[dict]:
    if not SKILLS_DIR.exists():
        return []
    files = []
    for path in SKILLS_DIR.iterdir():
        if not path.is_file() or path.suffix.lower() != ".md":
            continue
        stat = path.stat()
        files.append(
            {
                "name": path.name,
                "url": f"/skills/{quote(path.name)}",
                "size_bytes": stat.st_size,
                "updated_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            }
        )
    return sorted(files, key=lambda item: item["name"].lower())


def read_skill(filename: str) -> dict:
    path = resolve_skill_path(filename)
    stat = path.stat()
    return {
        "name": path.name,
        "path": str(path.relative_to(BASE_DIR)) if BASE_DIR in path.resolve().parents else str(path),
        "url": f"/skills/{quote(path.name)}",
        "content": normalize_skill_content(path.read_text(encoding="utf-8")),
        "size_bytes": stat.st_size,
        "updated_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
    }


def resolve_skill_path(filename: str):
    try:
        resolved = resolve_skill_target_path(filename)
    except ValueError:
        raise HTTPException(status_code=404, detail="Skill not found")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="Skill not found")
    return resolved


def resolve_skill_target_path(filename: str):
    normalized = normalize_skill_filename(filename)
    root = SKILLS_DIR.resolve()
    resolved = (SKILLS_DIR / normalized).resolve()
    if resolved.parent != root:
        raise ValueError("skill path must stay inside skills directory")
    return resolved


def normalize_skill_filename(filename: str) -> str:
    value = (filename or "").strip()
    if not value or "/" in value or "\\" in value:
        raise ValueError("skill name is required")
    if any(char in value for char in '<>:"|?*') or any(ord(char) < 32 for char in value):
        raise ValueError("skill name contains invalid characters")
    candidate = Path(value)
    if candidate.name != value:
        raise ValueError("skill name must not include a path")
    if not candidate.suffix:
        value = f"{value}.md"
        candidate = Path(value)
    if candidate.suffix.lower() != ".md":
        raise ValueError("skill name must end with .md")
    return value


def normalize_skill_content(content: str) -> str:
    return re.sub(r"\r+\n", "\n", content or "").replace("\r", "\n")


def wants_json_response(request: Request) -> bool:
    return (
        request.headers.get("x-requested-with", "").lower() == "xmlhttprequest"
        or "application/json" in request.headers.get("accept", "").lower()
    )


def submit_payload(conversation_id: str, user_message: dict, assistant_message: dict) -> dict:
    return {
        "conversation_id": conversation_id,
        "conversation_url": f"/conversations/{conversation_id}",
        "user_message": user_message,
        "assistant_message": assistant_message,
    }


async def uploaded_files_from_request(request: Request) -> list[UploadFile]:
    form = await request.form()
    return [item for item in form.getlist("files") if hasattr(item, "filename") and item.filename]


async def save_uploaded_files(conversation_id: str, message_id: str, uploads: list[UploadFile]) -> list[dict]:
    if not uploads:
        return []

    target_dir = (BASE_DIR / "data" / "uploads" / conversation_id / message_id).resolve()
    uploads_root = (BASE_DIR / "data" / "uploads").resolve()
    if uploads_root != target_dir and uploads_root not in target_dir.parents:
        raise HTTPException(status_code=400, detail="Invalid upload path")
    target_dir.mkdir(parents=True, exist_ok=True)

    image_upload_count = sum(1 for upload in uploads if image_mime_type(upload.filename or ""))
    if image_upload_count > MAX_IMAGES:
        raise HTTPException(status_code=400, detail=f"最多同时上传 {MAX_IMAGES} 张图片")

    attachments = []
    used_names: set[str] = set()
    for upload in uploads:
        filename = unique_filename(safe_upload_filename(upload.filename or "upload"), used_names, target_dir)
        path = target_dir / filename
        size_bytes = 0
        image_type = image_mime_type(filename)
        mime_type = image_type or upload.content_type or guess_media_type(filename)
        try:
            with path.open("wb") as handle:
                while True:
                    chunk = await upload.read(1024 * 1024)
                    if not chunk:
                        break
                    size_bytes += len(chunk)
                    if image_type and size_bytes > MAX_IMAGE_BYTES:
                        raise HTTPException(
                            status_code=413,
                            detail=f"图片 {filename} 超过 {MAX_IMAGE_BYTES // (1024 * 1024)} MB 限制",
                        )
                    handle.write(chunk)
        except HTTPException:
            with suppress(OSError):
                path.unlink()
            raise
        except OSError as exc:
            with suppress(OSError):
                path.unlink()
            reason = exc.strerror or str(exc)
            raise HTTPException(status_code=500, detail=f"Failed to save upload '{filename}': {reason}") from exc
        finally:
            await upload.close()

        rel_path = path.relative_to(BASE_DIR).as_posix()
        attachments.append(
            {
                "name": filename,
                "path": rel_path,
                "url": f"/uploads/{conversation_id}/{message_id}/{quote(filename)}",
                "mime_type": mime_type,
                "size_bytes": size_bytes,
                "is_image": mime_type.startswith("image/"),
            }
        )
    return attachments


def compose_prompt_with_attachments(prompt: str, attachments: list[dict]) -> str:
    lines = []
    cleaned_prompt = prompt.strip()
    if cleaned_prompt:
        lines.append(cleaned_prompt)
        lines.append("")
    lines.append("[上传文件]")
    has_image = any(attachment.get("is_image") for attachment in attachments)
    if has_image:
        lines.append("如需理解图片内容，可调用 view_images，传入图片的工作区路径和分析提示词。")
    for index, attachment in enumerate(attachments, start=1):
        lines.extend(
            [
                f"{index}. 文件名: {attachment['name']}",
                f"   类型: {attachment['mime_type']}",
                f"   大小: {attachment['size_bytes']} bytes",
                f"   工作区路径: {attachment['path']}",
            ]
        )
    return "\n".join(lines)


def safe_upload_filename(filename: str) -> str:
    name = Path(filename).name.strip().strip(".")
    if not name:
        name = "upload"
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name) or "upload"


def unique_filename(filename: str, used_names: set[str], target_dir: Path) -> str:
    candidate = filename
    stem = Path(filename).stem or "upload"
    suffix = Path(filename).suffix
    index = 1
    while candidate.lower() in used_names or (target_dir / candidate).exists():
        candidate = f"{stem}-{index}{suffix}"
        index += 1
    used_names.add(candidate.lower())
    return candidate


def ensure_not_cancelled(conversation_id: str, assistant_message_id: str, cancel_event: threading.Event) -> None:
    if cancel_event.is_set():
        raise ConversationCancelled()
    conversation = store.get_conversation(conversation_id)
    if not conversation:
        return
    for message in conversation["messages"]:
        if message["id"] == assistant_message_id and message.get("status") == CANCELLED_STATUS:
            cancel_event.set()
            raise ConversationCancelled()


def run_conversation_turn(conversation_id: str, assistant_message_id: str) -> None:
    cancel_event = running_conversations.start(conversation_id, assistant_message_id)

    try:
        ensure_not_cancelled(conversation_id, assistant_message_id, cancel_event)
        store.update_conversation(conversation_id, status="running", error="")
        store.update_message(conversation_id, assistant_message_id, status="running")
        app_config = app_settings_store.get()
        client = create_llm_client(app_config)
        tool_runner = AgentToolRunner(
            base_dir=BASE_DIR,
            scheduled_tasks=scheduled_task_store,
            memories=memory_store,
            task_store=store,
            python_timeout_seconds=app_config["python_timeout_seconds"],
            dingtalk_webhook_url=settings.dingtalk_webhook_url,
            dingtalk_access_token=settings.dingtalk_access_token,
            dingtalk_public_base_url=settings.dingtalk_public_base_url,
            mimo_api_key=settings.mimo_api_key,
            mimo_base_url=settings.mimo_base_url,
            disabled_tools=tool_settings_store.disabled_tools(),
            cancellation_event=cancel_event,
        )
        messages = build_model_context(conversation_id, assistant_message_id, app_config["system_prompt"])
        max_tool_rounds = app_config["max_tool_rounds"]
        tool_rounds = 0

        while True:
            ensure_not_cancelled(conversation_id, assistant_message_id, cancel_event)
            requested_tools = False
            assistant_protocol_reasoning = ""
            assistant_protocol_content = ""
            for event in client.stream_agent_turn(messages, tool_runner.definitions):
                ensure_not_cancelled(conversation_id, assistant_message_id, cancel_event)
                if event["type"] == "reasoning":
                    assistant_protocol_reasoning += event["delta"]
                    store.append_reasoning(conversation_id, assistant_message_id, event["delta"])
                elif event["type"] == "answer":
                    assistant_protocol_content += event["delta"]
                    store.append_answer(conversation_id, assistant_message_id, event["delta"])
                elif event["type"] == "tool_calls":
                    requested_tools = True
                    tool_rounds += 1
                    if tool_rounds > max_tool_rounds:
                        raise RuntimeError(f"Exceeded max tool rounds: {max_tool_rounds}")
                    tool_calls = normalize_tool_calls(event["tool_calls"])
                    store.attach_tool_calls(conversation_id, assistant_message_id, tool_calls)
                    assistant_message: dict = {
                        "role": "assistant",
                        "content": assistant_protocol_content,
                        "reasoning_content": assistant_protocol_reasoning,
                        "tool_calls": tool_calls,
                    }
                    messages.append(assistant_message)
                    store.append_api_message(conversation_id, assistant_message_id, assistant_message)
                    for tool_call in tool_calls:
                        ensure_not_cancelled(conversation_id, assistant_message_id, cancel_event)
                        function = tool_call["function"]
                        arguments = parse_tool_arguments(function.get("arguments", ""))
                        result = tool_runner.run(function["name"], arguments)
                        if result.get("cancelled"):
                            raise ConversationCancelled()
                        store.append_tool_message(
                            conversation_id=conversation_id,
                            assistant_message_id=assistant_message_id,
                            tool_call_id=tool_call["id"],
                            name=function["name"],
                            arguments=arguments,
                            result=result,
                            status="succeeded" if result.get("ok") else "failed",
                        )
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_call["id"],
                                "content": json.dumps(result, ensure_ascii=False),
                            }
                        )
                        store.append_api_message(conversation_id, assistant_message_id, messages[-1])
                    break

            if not requested_tools:
                ensure_not_cancelled(conversation_id, assistant_message_id, cancel_event)
                if assistant_protocol_content or assistant_protocol_reasoning:
                    store.append_api_message(
                        conversation_id,
                        assistant_message_id,
                        {
                            "role": "assistant",
                            "content": assistant_protocol_content,
                            "reasoning_content": assistant_protocol_reasoning,
                        },
                    )
                break

        ensure_not_cancelled(conversation_id, assistant_message_id, cancel_event)
        store.update_message(conversation_id, assistant_message_id, status="succeeded")
        store.update_conversation(conversation_id, status="succeeded", error="")
    except ConversationCancelled:
        store.update_message(conversation_id, assistant_message_id, status=CANCELLED_STATUS)
        store.update_conversation(conversation_id, status=CANCELLED_STATUS, error="")
    except Exception as exc:
        conversation = store.get_conversation(conversation_id)
        assistant = latest_assistant(conversation)
        if assistant and assistant.get("id") == assistant_message_id and assistant.get("status") == CANCELLED_STATUS:
            store.update_conversation(conversation_id, status=CANCELLED_STATUS, error="")
        else:
            store.update_message(conversation_id, assistant_message_id, status="failed")
            store.update_conversation(conversation_id, status="failed", error=str(exc))
    finally:
        running_conversations.finish(conversation_id, assistant_message_id, cancel_event)


def build_model_context(conversation_id: str, assistant_message_id: str, system_prompt: str) -> list[dict]:
    messages: list[dict] = []
    system_content = build_system_prompt(system_prompt, memory_store.list_memories())
    if system_content:
        messages.append({"role": "system", "content": system_content})
    messages.extend(store.chat_context(conversation_id, assistant_message_id))
    return messages


def current_context_token_estimate(conversation_id: str) -> int:
    """Estimate the tokens that the next model turn would receive for a conversation."""
    app_config = app_settings_store.get()
    messages = build_model_context(conversation_id, "", app_config["system_prompt"])
    return estimate_message_tokens(messages)


def estimate_message_tokens(messages: list[dict]) -> int:
    """Return a model-agnostic, intentionally approximate token count for API messages."""
    serialized = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
    cjk_pattern = r"[\u3400-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]"
    cjk_characters = len(re.findall(cjk_pattern, serialized))
    remaining = re.sub(cjk_pattern, " ", serialized)
    units = re.findall(r"[A-Za-z0-9_]+|[^\s]", remaining)
    non_cjk_tokens = sum(
        max(1, (len(unit) + 3) // 4) if unit[0].isalnum() or unit[0] == "_" else 1
        for unit in units
    )
    return cjk_characters + non_cjk_tokens


def build_system_prompt(system_prompt: str, memories: list[dict]) -> str:
    parts = []
    if system_prompt.strip():
        parts.append(system_prompt.strip())
    memory_block = format_memory_block(memories)
    if memory_block:
        parts.append(memory_block)
    return "\n\n".join(parts)


def create_llm_client(app_config: dict) -> OpenAICompatibleClient:
    provider = app_config["llm_provider"]
    model = app_config["llm_model"] or settings.deepseek_model
    custom_model = custom_model_by_provider(provider, app_config.get("custom_models", []))
    if custom_model:
        api_key = custom_model["api_key"]
        base_url = custom_model["base_url"]
        model = custom_model["model"]
    elif provider == MIMO_PROVIDER:
        api_key = settings.mimo_api_key
        base_url = settings.mimo_base_url
    else:
        api_key = settings.deepseek_api_key
        base_url = settings.deepseek_base_url
    return OpenAICompatibleClient(
        api_key=api_key,
        base_url=base_url,
        model=model,
        request_options=request_options_for_provider(provider),
    )


def format_memory_block(memories: list[dict]) -> str:
    lines = []
    for memory in memories:
        content = (memory.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"{format_memory_time(memory.get('updated_at', ''))} {memory.get('id', '')} {content}".strip())
    WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    _now = datetime.now(tz=ZoneInfo("Asia/Shanghai"))
    now_str = _now.strftime("%Y-%m-%d") + " " + WEEKDAYS[_now.weekday()]
    if not lines:
        return f"今天的日期是：{now_str}"
    return f"今天的日期是：{now_str}，你拥有的记忆：\n" + "\n".join(lines)


def format_memory_time(value: str) -> str:
    try:
        return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return value[:16] if value else ""


async def scheduler_loop() -> None:
    while True:
        for task in scheduled_task_store.claim_due_tasks():
            scheduled = asyncio.create_task(run_scheduled_task(task))
            scheduled.add_done_callback(log_scheduled_task_failure)
        await asyncio.sleep(20)


async def run_scheduled_task(task: dict) -> None:
    conversation = None
    try:
        scheduled_prompt = f"{SCHEDULED_TASK_PROMPT_PREFIX}{task['prompt']}"
        conversation = store.create_conversation(scheduled_prompt)
        assistant = conversation["messages"][-1]
        await asyncio.to_thread(run_conversation_turn, conversation["id"], assistant["id"])
        updated = store.get_conversation(conversation["id"])
        status = (updated or {}).get("status", "unknown")
        error = (updated or {}).get("error", "")
        scheduled_task_store.mark_result(task["id"], f"conversation:{conversation['id']} status:{status}", error)
    except Exception as exc:
        if conversation:
            with suppress(Exception):
                store.update_conversation(conversation["id"], status="failed", error=str(exc))
            scheduled_task_store.mark_result(task["id"], f"conversation:{conversation['id']} status:failed", str(exc))
        else:
            scheduled_task_store.mark_result(task["id"], "failed before conversation", str(exc))

    # Auto-delete one-time tasks after execution
    if task.get("schedule_type") == "once" and not task.get("enabled", True) and task.get("auto_delete", True):
        scheduled_task_store.delete_task(task["id"])


def log_scheduled_task_failure(task: asyncio.Task) -> None:
    with suppress(asyncio.CancelledError):
        exc = task.exception()
        if exc:
            print(f"Scheduled task crashed: {exc}")


def normalize_tool_calls(tool_calls: list[dict]) -> list[dict]:
    normalized = []
    for index, tool_call in enumerate(tool_calls):
        function = tool_call.get("function", {})
        normalized.append(
            {
                "id": tool_call.get("id") or f"tool_call_{index}",
                "type": tool_call.get("type") or "function",
                "function": {
                    "name": function.get("name", ""),
                    "arguments": function.get("arguments", "") or "{}",
                },
            }
        )
    return normalized


def latest_assistant(conversation: dict | None) -> dict | None:
    if not conversation:
        return None
    for message in reversed(conversation["messages"]):
        if message["role"] == "assistant":
            return message
    return None


def current_tool_messages(conversation: dict | None, assistant_id: str) -> list[dict]:
    if not conversation:
        return []
    messages = conversation["messages"]
    for index, message in enumerate(messages):
        if message["id"] == assistant_id:
            return [item for item in messages[index + 1 :] if item["role"] == "tool"]
    return []


def sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def is_safe_file_id(value: str) -> bool:
    return len(value) == 32 and all(char in "0123456789abcdef" for char in value)


def is_safe_upload_id(value: str) -> bool:
    return bool(value) and len(value) <= 64 and all(char in "0123456789abcdef" for char in value)


def guess_media_type(filename: str) -> str:
    import mimetypes

    media_type, _ = mimetypes.guess_type(filename)
    return media_type or "application/octet-stream"


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
