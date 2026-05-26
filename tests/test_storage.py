import json

from app.storage import TaskStore


def test_conversation_store_appends_multiturn_messages(tmp_path):
    store = TaskStore(tmp_path / "tasks.json")
    conversation = store.create_conversation("hello")
    assistant = conversation["messages"][-1]

    store.append_reasoning(conversation["id"], assistant["id"], "think ")
    store.append_answer(conversation["id"], assistant["id"], "answer")
    store.update_message(conversation["id"], assistant["id"], status="succeeded")
    store.append_user_message(conversation["id"], "next")
    second_assistant = store.create_assistant_message(conversation["id"])

    updated = store.get_conversation(conversation["id"])

    assert [message["role"] for message in updated["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert updated["messages"][1]["reasoning_content"] == "think "
    assert updated["messages"][1]["content"] == "answer"
    assert second_assistant["status"] == "queued"


def test_legacy_task_json_is_normalized_to_conversation(tmp_path):
    path = tmp_path / "tasks.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "legacy",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-01T00:00:01+00:00",
                    "status": "succeeded",
                    "prompt": "old prompt",
                    "reasoning_content": "old thought",
                    "answer": "old answer",
                    "error": "",
                    "dingding_result": {"errcode": 0},
                }
            ]
        ),
        encoding="utf-8",
    )

    store = TaskStore(path)
    conversation = store.get_conversation("legacy")

    assert conversation["title"] == "old prompt"
    assert conversation["messages"][0]["role"] == "user"
    assert conversation["messages"][0]["content"] == "old prompt"
    assert conversation["messages"][1]["role"] == "assistant"
    assert conversation["messages"][1]["content"] == "old answer"
    assert conversation["messages"][1]["reasoning_content"] == "old thought"
