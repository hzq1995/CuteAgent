import asyncio
import json

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.config import BASE_DIR, get_settings
from app.deepseek_client import DeepSeekClient
from app.storage import TaskStore
from utils.dingding_robot import DingdingRobot


BUSINESS_NOTICE_PREFIX = "[业务通知]"

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
store = TaskStore(BASE_DIR / "data" / "tasks.json")


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
        return RedirectResponse("/", status_code=303)

    conversation = store.create_conversation(cleaned_prompt)
    assistant = conversation["messages"][-1]
    background_tasks.add_task(run_conversation_turn, conversation["id"], assistant["id"])
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
        return RedirectResponse(f"/conversations/{conversation_id}", status_code=303)

    conversation = store.get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if store.has_running_message(conversation_id):
        return RedirectResponse(f"/conversations/{conversation_id}", status_code=303)

    store.append_user_message(conversation_id, cleaned_prompt)
    assistant = store.create_assistant_message(conversation_id)
    background_tasks.add_task(run_conversation_turn, conversation_id, assistant["id"])
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
        sent_status = ""
        sent_dingding = False

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
                assistant_id = assistant["id"]
                sent_status = ""
                sent_dingding = False
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

            if assistant.get("dingding_result") is not None and not sent_dingding:
                sent_dingding = True
                yield sse(
                    "dingding",
                    {"message_id": assistant_id, "result": assistant["dingding_result"]},
                )

            if conversation.get("error"):
                yield sse("error", {"message_id": assistant_id, "error": conversation["error"]})
                break

            if assistant["status"] == "failed":
                yield sse("done", {"status": "failed", "message_id": assistant_id})
                break
            if assistant["status"] == "succeeded" and sent_dingding:
                yield sse("done", {"status": "succeeded", "message_id": assistant_id})
                break

            await asyncio.sleep(0.35)

    return StreamingResponse(events(), media_type="text/event-stream")


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
    conversations = store.list_conversations()
    active_assistant = latest_assistant(conversation) if conversation else None
    reasoning_offset = len(active_assistant.get("reasoning_content", "")) if active_assistant else 0
    answer_offset = len(active_assistant.get("content", "")) if active_assistant else 0
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "conversation": conversation,
            "conversations": conversations,
            "is_running": bool(conversation and store.has_running_message(conversation["id"])),
            "active_assistant": active_assistant,
            "reasoning_offset": reasoning_offset,
            "answer_offset": answer_offset,
        },
    )


def run_conversation_turn(conversation_id: str, assistant_message_id: str) -> None:
    store.update_conversation(conversation_id, status="running", error="")
    store.update_message(conversation_id, assistant_message_id, status="running")

    try:
        client = DeepSeekClient(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
        )
        messages = store.chat_context(conversation_id, assistant_message_id)
        for kind, delta in client.stream_chat(messages):
            if kind == "reasoning":
                store.append_reasoning(conversation_id, assistant_message_id, delta)
            elif kind == "answer":
                store.append_answer(conversation_id, assistant_message_id, delta)

        store.update_message(conversation_id, assistant_message_id, status="succeeded")
        store.update_conversation(conversation_id, status="succeeded", error="")
    except Exception as exc:
        store.update_message(conversation_id, assistant_message_id, status="failed")
        store.update_conversation(conversation_id, status="failed", error=str(exc))
        return

    conversation = store.get_conversation(conversation_id)
    assistant = find_message(conversation, assistant_message_id) if conversation else None
    answer = (assistant or {}).get("content") or "(empty reply)"
    robot = DingdingRobot(
        webhook_url=settings.dingtalk_webhook_url,
        access_token=settings.dingtalk_access_token,
    )
    dingding_result = robot.send_markdown(
        f"{BUSINESS_NOTICE_PREFIX} CuteHarness AI Reply",
        f"{BUSINESS_NOTICE_PREFIX}\n\n{answer}",
    )
    store.attach_dingding_result(conversation_id, assistant_message_id, dingding_result)


def latest_assistant(conversation: dict) -> dict | None:
    for message in reversed(conversation["messages"]):
        if message["role"] == "assistant":
            return message
    return None


def find_message(conversation: dict, message_id: str) -> dict | None:
    for message in conversation["messages"]:
        if message["id"] == message_id:
            return message
    return None


def sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
