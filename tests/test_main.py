import copy
import asyncio

from app import main


def test_run_conversation_turn_uses_tool_call_for_dingtalk(monkeypatch, tmp_path):
    sent = {}
    store = main.TaskStore(tmp_path / "conversations")
    scheduled = main.ScheduledTaskStore(tmp_path / "scheduled_tasks.json")
    app_settings = main.AppSettingsStore(tmp_path / "settings.json")
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
    conversation = store.create_conversation("hello")
    assistant = conversation["messages"][-1]

    monkeypatch.setattr(main, "store", store)

    assert main.build_model_context(conversation["id"], assistant["id"], "be useful") == [
        {"role": "system", "content": "be useful"},
        {"role": "user", "content": "hello"},
    ]


def test_run_scheduled_task_creates_visible_conversation(monkeypatch, tmp_path):
    store = main.TaskStore(tmp_path / "conversations")
    scheduled = main.ScheduledTaskStore(tmp_path / "scheduled_tasks.json")
    app_settings = main.AppSettingsStore(tmp_path / "settings.json")
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
