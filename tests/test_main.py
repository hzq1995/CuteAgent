from app import main


def test_run_task_pushes_business_notice_prefix(monkeypatch, tmp_path):
    sent = {}
    store = main.TaskStore(tmp_path / "tasks.json")
    task = store.create_task("hello")

    class FakeDeepSeekClient:
        def __init__(self, **kwargs):
            pass

        def stream_chat(self, prompt):
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

    main.run_task(task["id"])

    assert sent["title"].startswith("[业务通知]")
    assert sent["text"].startswith("[业务通知]")
