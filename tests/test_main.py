import copy
import asyncio

from fastapi.testclient import TestClient

from app import main


def login(client):
    response = client.post("/login", data={"password": main.settings.app_password})
    assert response.status_code == 200


def json_headers():
    return {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"}


def test_run_conversation_turn_uses_tool_call_for_dingtalk(monkeypatch, tmp_path):
    sent = {}
    store = main.TaskStore(tmp_path / "conversations")
    scheduled = main.ScheduledTaskStore(tmp_path / "scheduled_tasks.json")
    app_settings = main.AppSettingsStore(tmp_path / "settings.json")
    memories = main.MemoryStore(tmp_path / "memories.json")
    conversation = store.create_conversation("send a notice")
    assistant = conversation["messages"][-1]

    class FakeDeepSeekClient:
        def __init__(self, **kwargs):
            self.calls = 0

        def stream_agent_turn(self, messages, tools):
            self.calls += 1
            sent.setdefault("messages", []).append(copy.deepcopy(messages))
            if self.calls == 1:
                yield {"type": "reasoning", "delta": "thinking"}
                yield {
                    "type": "tool_calls",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "send_dingtalk_message",
                                "arguments": '{"title":"Hello","text":"World"}',
                            },
                        }
                    ],
                }
            else:
                yield {"type": "answer", "delta": "done"}
                yield {"type": "done", "finish_reason": "stop"}

    class FakeToolRunner:
        def __init__(self, **kwargs):
            pass

        def run(self, name, arguments):
            sent["tool_name"] = name
            sent["tool_arguments"] = arguments
            return {"ok": True, "result": {"errcode": 0}}

    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "scheduled_task_store", scheduled)
    monkeypatch.setattr(main, "app_settings_store", app_settings)
    monkeypatch.setattr(main, "memory_store", memories)
    monkeypatch.setattr(main, "DeepSeekClient", FakeDeepSeekClient)
    monkeypatch.setattr(main, "AgentToolRunner", FakeToolRunner)

    main.run_conversation_turn(conversation["id"], assistant["id"])
    updated = store.get_conversation(conversation["id"])

    assert sent["messages"][0] == [{"role": "user", "content": "send a notice"}]
    assert sent["messages"][1][1] == {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "send_dingtalk_message",
                    "arguments": '{"title":"Hello","text":"World"}',
                },
            }
        ],
        "reasoning_content": "thinking",
    }
    assert sent["tool_name"] == "send_dingtalk_message"
    assert sent["tool_arguments"] == {"title": "Hello", "text": "World"}
    assert [part["type"] for part in updated["messages"][-2]["parts"]] == ["reasoning", "tool", "answer"]
    assert updated["messages"][-2]["parts"][1]["name"] == "send_dingtalk_message"
    assert updated["messages"][-2]["content"] == "done"
    assert updated["messages"][-2]["api_messages"] == [
        {
            "role": "assistant",
            "content": "",
            "reasoning_content": "thinking",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "send_dingtalk_message",
                        "arguments": '{"title":"Hello","text":"World"}',
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "{\"ok\": true, \"result\": {\"errcode\": 0}}"},
        {"role": "assistant", "content": "done", "reasoning_content": ""},
    ]
    assert updated["messages"][-1]["role"] == "tool"
    assert updated["messages"][-1]["inline_rendered"] is True
    assert updated["messages"][-1]["result"] == {"ok": True, "result": {"errcode": 0}}

    store.append_user_message(conversation["id"], "thanks")
    next_assistant = store.create_assistant_message(conversation["id"])
    assert store.chat_context(conversation["id"], next_assistant["id"]) == [
        {"role": "user", "content": "send a notice"},
        updated["messages"][-2]["api_messages"][0],
        updated["messages"][-2]["api_messages"][1],
        updated["messages"][-2]["api_messages"][2],
        {"role": "user", "content": "thanks"},
    ]


def test_build_model_context_includes_system_prompt(monkeypatch, tmp_path):
    store = main.TaskStore(tmp_path / "conversations")
    memories = main.MemoryStore(tmp_path / "memories.json")
    conversation = store.create_conversation("hello")
    assistant = conversation["messages"][-1]

    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "memory_store", memories)

    assert main.build_model_context(conversation["id"], assistant["id"], "be useful") == [
        {"role": "system", "content": "be useful"},
        {"role": "user", "content": "hello"},
    ]


def test_build_model_context_appends_memories_after_system_prompt(monkeypatch, tmp_path):
    store = main.TaskStore(tmp_path / "conversations")
    memories = main.MemoryStore(tmp_path / "memories.json")
    memory = memories.add_memory("用户喜欢简洁中文回答")["memory"]
    conversation = store.create_conversation("hello")
    assistant = conversation["messages"][-1]

    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "memory_store", memories)

    context = main.build_model_context(conversation["id"], assistant["id"], "be useful")

    assert context[0]["role"] == "system"
    assert context[0]["content"].startswith("be useful\n\n记忆：\n")
    assert f"{memory['id']} 用户喜欢简洁中文回答" in context[0]["content"]
    assert context[1] == {"role": "user", "content": "hello"}


def test_build_model_context_injects_memories_without_system_prompt(monkeypatch, tmp_path):
    store = main.TaskStore(tmp_path / "conversations")
    memories = main.MemoryStore(tmp_path / "memories.json")
    memory = memories.add_memory("用户使用 FastAPI")["memory"]
    conversation = store.create_conversation("hello")
    assistant = conversation["messages"][-1]

    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "memory_store", memories)

    context = main.build_model_context(conversation["id"], assistant["id"], "")

    assert context[0]["role"] == "system"
    assert context[0]["content"].startswith("记忆：\n")
    assert f"{memory['id']} 用户使用 FastAPI" in context[0]["content"]
    assert context[1] == {"role": "user", "content": "hello"}


def test_run_scheduled_task_creates_visible_conversation(monkeypatch, tmp_path):
    store = main.TaskStore(tmp_path / "conversations")
    scheduled = main.ScheduledTaskStore(tmp_path / "scheduled_tasks.json")
    app_settings = main.AppSettingsStore(tmp_path / "settings.json")
    memories = main.MemoryStore(tmp_path / "memories.json")
    task = scheduled.create_task("notice", "send scheduled notice", "once", "2026-12-01 09:00", True)

    class FakeDeepSeekClient:
        def __init__(self, **kwargs):
            pass

        def stream_agent_turn(self, messages, tools):
            yield {"type": "answer", "delta": "scheduled done"}
            yield {"type": "done", "finish_reason": "stop"}

    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "scheduled_task_store", scheduled)
    monkeypatch.setattr(main, "app_settings_store", app_settings)
    monkeypatch.setattr(main, "memory_store", memories)
    monkeypatch.setattr(main, "DeepSeekClient", FakeDeepSeekClient)

    asyncio.run(main.run_scheduled_task(task))

    conversations = store.list_conversations()
    updated_task = scheduled.get_task(task["id"])
    assert len(conversations) == 1
    assert conversations[0]["messages"][0]["content"] == "send scheduled notice"
    assert conversations[0]["messages"][-1]["content"] == "scheduled done"
    assert updated_task["last_result"] == f"conversation:{conversations[0]['id']} status:succeeded"
    assert updated_task["error"] == ""


def test_run_scheduled_task_marks_pre_conversation_failure(monkeypatch, tmp_path):
    store = main.TaskStore(tmp_path / "conversations")
    scheduled = main.ScheduledTaskStore(tmp_path / "scheduled_tasks.json")
    task = scheduled.create_task("notice", "send scheduled notice", "once", "2026-12-01 09:00", True)

    def fail_create_conversation(prompt):
        raise RuntimeError("disk unavailable")

    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "scheduled_task_store", scheduled)
    monkeypatch.setattr(store, "create_conversation", fail_create_conversation)

    asyncio.run(main.run_scheduled_task(task))

    updated_task = scheduled.get_task(task["id"])
    assert store.list_conversations() == []
    assert updated_task["last_result"] == "failed before conversation"
    assert updated_task["error"] == "disk unavailable"


def test_create_conversation_json_returns_message_ids(monkeypatch, tmp_path):
    store = main.TaskStore(tmp_path / "conversations")
    calls = []

    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "run_conversation_turn", lambda conversation_id, message_id: calls.append((conversation_id, message_id)))

    client = TestClient(main.app)
    login(client)

    response = client.post("/conversations", data={"prompt": "hello"}, headers=json_headers())

    assert response.status_code == 201
    payload = response.json()
    assert payload["conversation_url"] == f"/conversations/{payload['conversation_id']}"
    assert payload["user_message"]["role"] == "user"
    assert payload["user_message"]["content"] == "hello"
    assert payload["assistant_message"]["role"] == "assistant"
    assert payload["assistant_message"]["status"] in {"queued", "running", "succeeded"}
    assert calls == [(payload["conversation_id"], payload["assistant_message"]["id"])]


def test_append_message_json_returns_message_ids(monkeypatch, tmp_path):
    store = main.TaskStore(tmp_path / "conversations")
    conversation = store.create_conversation("hello")
    store.update_message(conversation["id"], conversation["messages"][-1]["id"], status="succeeded")
    calls = []

    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "run_conversation_turn", lambda conversation_id, message_id: calls.append((conversation_id, message_id)))

    client = TestClient(main.app)
    login(client)

    response = client.post(
        f"/conversations/{conversation['id']}/messages",
        data={"prompt": "next"},
        headers=json_headers(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["conversation_id"] == conversation["id"]
    assert payload["user_message"]["role"] == "user"
    assert payload["user_message"]["content"] == "next"
    assert payload["assistant_message"]["role"] == "assistant"
    assert payload["assistant_message"]["status"] in {"queued", "running", "succeeded"}
    assert calls == [(conversation["id"], payload["assistant_message"]["id"])]


def test_append_message_json_conflicts_when_running_but_form_redirects(monkeypatch, tmp_path):
    store = main.TaskStore(tmp_path / "conversations")
    conversation = store.create_conversation("hello")

    monkeypatch.setattr(main, "store", store)

    client = TestClient(main.app)
    login(client)

    json_response = client.post(
        f"/conversations/{conversation['id']}/messages",
        data={"prompt": "next"},
        headers=json_headers(),
    )
    form_response = client.post(
        f"/conversations/{conversation['id']}/messages",
        data={"prompt": "next"},
        follow_redirects=False,
    )

    assert json_response.status_code == 409
    assert json_response.json()["error"] == "Conversation already has a running message"
    assert form_response.status_code == 303
    assert form_response.headers["location"] == f"/conversations/{conversation['id']}"
