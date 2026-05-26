from app.deepseek_client import DeepSeekClient


class FakeDelta:
    def __init__(self, reasoning_content=None, content=None):
        self.reasoning_content = reasoning_content
        self.content = content


class FakeChoice:
    def __init__(self, delta):
        self.delta = delta


class FakeChunk:
    def __init__(self, delta):
        self.choices = [FakeChoice(delta)]


def test_stream_chat_uses_multiturn_v4_flash_thinking(monkeypatch):
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return [
                FakeChunk(FakeDelta(reasoning_content="think")),
                FakeChunk(FakeDelta(content="answer")),
            ]

    class FakeClient:
        def __init__(self, **kwargs):
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr("app.deepseek_client.OpenAI", FakeClient)

    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "again"},
    ]
    client = DeepSeekClient("key", "https://api.deepseek.com", "deepseek-v4-flash")

    assert list(client.stream_chat(messages)) == [("reasoning", "think"), ("answer", "answer")]
    assert captured["messages"] == messages
    assert captured["model"] == "deepseek-v4-flash"
    assert captured["stream"] is True
    assert captured["reasoning_effort"] == "high"
    assert captured["extra_body"] == {"thinking": {"type": "enabled"}}
