from collections.abc import Iterator
from typing import Any

from openai import OpenAI


class DeepSeekClient:
    def __init__(self, api_key: str, base_url: str, model: str):
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def stream_chat(self, messages: list[dict[str, str]]) -> Iterator[tuple[str, str]]:
        for event in self.stream_agent_turn(messages, tools=None):
            if event["type"] == "reasoning":
                yield "reasoning", event["delta"]
            elif event["type"] == "answer":
                yield "answer", event["delta"]

    def stream_agent_turn(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> Iterator[dict[str, Any]]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "reasoning_effort": "high",
            "extra_body": {"thinking": {"type": "enabled"}},
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        response = self.client.chat.completions.create(
            **kwargs,
        )
        tool_calls: dict[int, dict[str, Any]] = {}
        finish_reason = None
        for chunk in response:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            finish_reason = getattr(choice, "finish_reason", None) or finish_reason
            delta = choice.delta
            reasoning = getattr(delta, "reasoning_content", None)
            content = getattr(delta, "content", None)
            if reasoning:
                yield {"type": "reasoning", "delta": reasoning}
            if content:
                yield {"type": "answer", "delta": content}

            for tool_delta in getattr(delta, "tool_calls", None) or []:
                index = getattr(tool_delta, "index", 0) or 0
                current = tool_calls.setdefault(
                    index,
                    {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                )
                tool_id = getattr(tool_delta, "id", None)
                if tool_id:
                    current["id"] = tool_id
                tool_type = getattr(tool_delta, "type", None)
                if tool_type:
                    current["type"] = tool_type
                function = getattr(tool_delta, "function", None)
                if function:
                    name = getattr(function, "name", None)
                    arguments = getattr(function, "arguments", None)
                    if name:
                        current["function"]["name"] += name
                    if arguments:
                        current["function"]["arguments"] += arguments

        if finish_reason == "tool_calls":
            yield {"type": "tool_calls", "tool_calls": list(tool_calls.values())}
        else:
            yield {"type": "done", "finish_reason": finish_reason}
