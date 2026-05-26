from collections.abc import Iterator

from openai import OpenAI


class DeepSeekClient:
    def __init__(self, api_key: str, base_url: str, model: str):
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def stream_chat(self, prompt: str) -> Iterator[tuple[str, str]]:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            stream=True,
            reasoning_effort="high",
            extra_body={"thinking": {"type": "enabled"}},
        )
        for chunk in response:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            reasoning = getattr(delta, "reasoning_content", None)
            content = getattr(delta, "content", None)
            if reasoning:
                yield "reasoning", reasoning
            if content:
                yield "answer", content
