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
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "tasks": store.list_tasks()},
    )


@app.post("/tasks")
async def create_task(
    request: Request,
    background_tasks: BackgroundTasks,
    prompt: str = Form(...),
):
    require_login(request)
    cleaned_prompt = prompt.strip()
    if not cleaned_prompt:
        return RedirectResponse("/", status_code=303)

    task = store.create_task(cleaned_prompt)
    background_tasks.add_task(run_task, task["id"])
    return RedirectResponse(f"/tasks/{task['id']}", status_code=303)


@app.get("/tasks/{task_id}", response_class=HTMLResponse)
async def task_detail(task_id: str, request: Request):
    require_login(request)
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return templates.TemplateResponse("task.html", {"request": request, "task": task})


@app.get("/tasks/{task_id}/stream")
async def task_stream(task_id: str, request: Request, _: None = Depends(require_login)):
    async def events():
        sent_reasoning_len = 0
        sent_answer_len = 0
        sent_status = ""
        sent_dingding = False

        while True:
            if await request.is_disconnected():
                break

            task = store.get_task(task_id)
            if not task:
                yield sse("error", {"error": "Task not found"})
                break

            if task["status"] != sent_status:
                sent_status = task["status"]
                yield sse("status", {"status": sent_status})

            reasoning = task.get("reasoning_content") or ""
            if len(reasoning) > sent_reasoning_len:
                yield sse("reasoning", {"delta": reasoning[sent_reasoning_len:]})
                sent_reasoning_len = len(reasoning)

            answer = task.get("answer") or ""
            if len(answer) > sent_answer_len:
                yield sse("answer", {"delta": answer[sent_answer_len:]})
                sent_answer_len = len(answer)

            if task.get("error"):
                yield sse("error", {"error": task["error"]})
                break

            if task.get("dingding_result") is not None and not sent_dingding:
                sent_dingding = True
                yield sse("dingding", {"result": task["dingding_result"]})

            if task["status"] == "failed":
                yield sse("done", {"status": task["status"]})
                break
            if task["status"] == "succeeded" and sent_dingding:
                yield sse("done", {"status": task["status"]})
                break

            await asyncio.sleep(0.4)

    return StreamingResponse(events(), media_type="text/event-stream")


def run_task(task_id: str) -> None:
    store.update_task(task_id, status="running", error="")
    task = store.get_task(task_id)
    if not task:
        return

    try:
        client = DeepSeekClient(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
        )
        for kind, delta in client.stream_chat(task["prompt"]):
            if kind == "reasoning":
                store.append_reasoning(task_id, delta)
            elif kind == "answer":
                store.append_answer(task_id, delta)

        task = store.update_task(task_id, status="succeeded")
    except Exception as exc:
        store.update_task(task_id, status="failed", error=str(exc))
        return

    robot = DingdingRobot(
        webhook_url=settings.dingtalk_webhook_url,
        access_token=settings.dingtalk_access_token,
    )
    answer = task.get("answer") or "(empty reply)"
    dingding_result = robot.send_markdown("[业务通知] CuteHarness AI Reply", f"[业务通知]\n\n{answer}")
    store.update_task(task_id, dingding_result=dingding_result)


def sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
