from app import main


def test_run_conversation_turn_pushes_business_notice_prefix(monkeypatch, tmp_path):
    sent = {}
    store = main.TaskStore(tmp_path / "tasks.json")
    conversation = store.create_conversation("hello")
    assistant = conversation["messages"][-1]

    class FakeDeepSeekClient:
        def __init__(self, **kwargs):
            pass

        def stream_chat(self, messages):
            sent["messages"] = messages
            yield "reasoning", "private thought"
            yield "answer", "world"

    class FakeDingdingRobot:
        def __init__(self, **kwargs):
            pass

        def send_markdown(self, title, text):
            sent["title"] = title
            sent["text"] = text
            return {"errcode": 0}

    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "DeepSeekClient", FakeDeepSeekClient)
    monkeypatch.setattr(main, "DingdingRobot", FakeDingdingRobot)

    main.run_conversation_turn(conversation["id"], assistant["id"])
    updated = store.get_conversation(conversation["id"])

    assert sent["messages"] == [{"role": "user", "content": "hello"}]
    assert sent["title"].startswith("[业务通知]")
    assert sent["text"].startswith("[业务通知]")
    assert "private thought" not in sent["text"]
    assert updated["messages"][-1]["dingding_result"] == {"errcode": 0}
