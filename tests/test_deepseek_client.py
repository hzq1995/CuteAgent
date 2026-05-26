from app.deepseek_client import DeepSeekClient


class FakeDelta:
    def __init__(self, reasoning_content=None, content=None, tool_calls=None):
        self.reasoning_content = reasoning_content
        self.content = content
        self.tool_calls = tool_calls


class FakeFunctionDelta:
    def __init__(self, name=None, arguments=None):
        self.name = name
        self.arguments = arguments


class FakeToolCallDelta:
    def __init__(self, index=0, tool_id=None, name=None, arguments=None):
        self.index = index
        self.id = tool_id
        self.type = "function"
        self.function = FakeFunctionDelta(name=name, arguments=arguments)


class FakeChoice:
    def __init__(self, delta, finish_reason=None):
        self.delta = delta
        self.finish_reason = finish_reason


class FakeChunk:
    def __init__(self, delta, finish_reason=None):
        self.choices = [FakeChoice(delta, finish_reason)]


def test_stream_chat_uses_multiturn_v4_flash_thinking(monkeypatch):
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return [
                FakeChunk(FakeDelta(reasoning_content="think")),
                FakeChunk(FakeDelta(content="answer")),
                FakeChunk(FakeDelta(), finish_reason="stop"),
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


def test_stream_agent_turn_collects_streaming_tool_calls(monkeypatch):
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return [
                FakeChunk(FakeDelta(tool_calls=[FakeToolCallDelta(tool_id="call_1", name="run_python", arguments='{"code"')])),
                FakeChunk(FakeDelta(tool_calls=[FakeToolCallDelta(arguments=':"print(1)"}')])),
                FakeChunk(FakeDelta(), finish_reason="tool_calls"),
            ]

    class FakeClient:
        def __init__(self, **kwargs):
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr("app.deepseek_client.OpenAI", FakeClient)

    client = DeepSeekClient("key", "https://api.deepseek.com", "deepseek-v4-flash")
    events = list(client.stream_agent_turn([{"role": "user", "content": "calc"}], [{"type": "function"}]))

    assert captured["tool_choice"] == "auto"
    assert events[-1] == {
        "type": "tool_calls",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "run_python", "arguments": '{"code":"print(1)"}'},
            }
        ],
    }
