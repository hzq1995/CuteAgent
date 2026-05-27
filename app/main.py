import asyncio
import json
import time
from contextlib import suppress
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.agent_tools import TOOL_DEFINITIONS, AgentToolRunner, parse_tool_arguments
from app.app_settings import AppSettingsStore
from app.config import BASE_DIR, get_settings
from app.deepseek_client import DeepSeekClient
from app.memory_store import MemoryStore
from app.scheduler_store import ScheduledTaskStore
from app.storage import TaskStore


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
memory_store = MemoryStore(BASE_DIR / "data" / "memories.json")


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
    prompt: str = Form(...),
):
    require_login(request)
    cleaned_prompt = prompt.strip()
    if not cleaned_prompt:
        if wants_json_response(request):
            return JSONResponse({"error": "Prompt is required"}, status_code=400)
        return RedirectResponse("/", status_code=303)

    conversation = store.create_conversation(cleaned_prompt)
    user = conversation["messages"][0]
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
    prompt: str = Form(...),
):
    require_login(request)
    cleaned_prompt = prompt.strip()
    if not cleaned_prompt:
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
    assistant = store.create_assistant_message(conversation_id)
    background_tasks.add_task(run_conversation_turn, conversation_id, assistant["id"])
    if wants_json_response(request):
        return JSONResponse(submit_payload(conversation_id, user, assistant))
    return RedirectResponse(f"/conversations/{conversation_id}", status_code=303)


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
        sent_status = ""

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

            tools = current_tool_messages(conversation, assistant_id)
            if len(tools) > sent_tool_count:
                for tool_message in tools[sent_tool_count:]:
                    yield sse("tool_call_result", {"message_id": assistant_id, "message": tool_message})
                sent_tool_count = len(tools)

            if conversation.get("error"):
                yield sse("error", {"message_id": assistant_id, "error": conversation["error"]})
                break

            if assistant["status"] == "failed":
                yield sse("done", {"status": "failed", "message_id": assistant_id})
                break
            if assistant["status"] == "succeeded":
                yield sse("done", {"status": "succeeded", "message_id": assistant_id})
                break

            await asyncio.sleep(0.35)

    return StreamingResponse(events(), media_type="text/event-stream")


@app.get("/scheduled-tasks", response_class=HTMLResponse)
async def scheduled_tasks_page(request: Request):
    redirect = redirect_if_unauthenticated(request)
    if redirect:
        return redirect
    return templates.TemplateResponse(
        "scheduled_tasks.html",
        base_context(request, active_page="scheduled_tasks")
        | {"scheduled_tasks": scheduled_task_store.list_tasks(), "editing_task": None},
    )


@app.post("/scheduled-tasks")
async def create_scheduled_task(
    request: Request,
    title: str = Form(""),
    prompt: str = Form(...),
    schedule_type: str = Form(...),
    schedule_value: str = Form(...),
    enabled: str | None = Form(None),
):
    require_login(request)
    scheduled_task_store.create_task(title, prompt, schedule_type, schedule_value, enabled == "on")
    return RedirectResponse("/scheduled-tasks", status_code=303)


@app.get("/scheduled-tasks/{task_id}", response_class=HTMLResponse)
async def edit_scheduled_task_page(task_id: str, request: Request):
    require_login(request)
    task = scheduled_task_store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Scheduled task not found")
    return templates.TemplateResponse(
        "scheduled_tasks.html",
        base_context(request, active_page="scheduled_tasks")
        | {"scheduled_tasks": scheduled_task_store.list_tasks(), "editing_task": task},
    )


@app.post("/scheduled-tasks/{task_id}")
async def update_scheduled_task(
    task_id: str,
    request: Request,
    title: str = Form(""),
    prompt: str = Form(...),
    schedule_type: str = Form(...),
    schedule_value: str = Form(...),
    enabled: str | None = Form(None),
):
    require_login(request)
    scheduled_task_store.update_task(task_id, title, prompt, schedule_type, schedule_value, enabled == "on")
    return RedirectResponse("/scheduled-tasks", status_code=303)


@app.post("/scheduled-tasks/{task_id}/delete")
async def delete_scheduled_task(task_id: str, request: Request):
    require_login(request)
    scheduled_task_store.delete_task(task_id)
    return RedirectResponse("/scheduled-tasks", status_code=303)


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    redirect = redirect_if_unauthenticated(request)
    if redirect:
        return redirect
    return templates.TemplateResponse(
        "settings.html",
        base_context(request, active_page="settings")
        | {"app_settings": app_settings_store.get(), "memories": memory_store.list_memories(), "saved": False},
    )


@app.post("/settings", response_class=HTMLResponse)
async def update_settings(
    request: Request,
    system_prompt: str = Form(""),
    python_timeout_seconds: int = Form(30),
    max_tool_rounds: int = Form(5),
):
    require_login(request)
    values = app_settings_store.update(system_prompt, python_timeout_seconds, max_tool_rounds)
    return templates.TemplateResponse(
        "settings.html",
        base_context(request, active_page="settings")
        | {"app_settings": values, "memories": memory_store.list_memories(), "saved": True},
    )


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
        },
    )


def base_context(request: Request, active_page: str) -> dict:
    return {
        "request": request,
        "conversations": store.list_conversations(),
        "active_page": active_page,
    }


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


def run_conversation_turn(conversation_id: str, assistant_message_id: str) -> None:
    store.update_conversation(conversation_id, status="running", error="")
    store.update_message(conversation_id, assistant_message_id, status="running")

    try:
        app_config = app_settings_store.get()
        client = DeepSeekClient(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
        )
        tool_runner = AgentToolRunner(
            base_dir=BASE_DIR,
            scheduled_tasks=scheduled_task_store,
            memories=memory_store,
            task_store=store,
            python_timeout_seconds=app_config["python_timeout_seconds"],
            dingtalk_webhook_url=settings.dingtalk_webhook_url,
            dingtalk_access_token=settings.dingtalk_access_token,
        )
        messages = build_model_context(conversation_id, assistant_message_id, app_config["system_prompt"])
        max_tool_rounds = app_config["max_tool_rounds"]
        tool_rounds = 0

        while True:
            requested_tools = False
            assistant_protocol_reasoning = ""
            assistant_protocol_content = ""
            for event in client.stream_agent_turn(messages, TOOL_DEFINITIONS):
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
                        function = tool_call["function"]
                        arguments = parse_tool_arguments(function.get("arguments", ""))
                        result = tool_runner.run(function["name"], arguments)
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

        store.update_message(conversation_id, assistant_message_id, status="succeeded")
        store.update_conversation(conversation_id, status="succeeded", error="")
    except Exception as exc:
        store.update_message(conversation_id, assistant_message_id, status="failed")
        store.update_conversation(conversation_id, status="failed", error=str(exc))


def build_model_context(conversation_id: str, assistant_message_id: str, system_prompt: str) -> list[dict]:
    messages: list[dict] = []
    system_content = build_system_prompt(system_prompt, memory_store.list_memories())
    if system_content:
        messages.append({"role": "system", "content": system_content})
    messages.extend(store.chat_context(conversation_id, assistant_message_id))
    return messages


def build_system_prompt(system_prompt: str, memories: list[dict]) -> str:
    parts = []
    if system_prompt.strip():
        parts.append(system_prompt.strip())
    memory_block = format_memory_block(memories)
    if memory_block:
        parts.append(memory_block)
    return "\n\n".join(parts)


def format_memory_block(memories: list[dict]) -> str:
    lines = []
    for memory in memories:
        content = (memory.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"{format_memory_time(memory.get('updated_at', ''))} {memory.get('id', '')} {content}".strip())
    now_str = datetime.now(tz=ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M")
    if not lines:
        return f"现在的时间是：{now_str}"
    return f"现在的时间是：{now_str}，你拥有的记忆：\n" + "\n".join(lines)


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
        conversation = store.create_conversation(task["prompt"])
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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
