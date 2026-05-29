from app.llm_client import OpenAICompatibleClient
from app.llm_config import request_options_for_provider


class DeepSeekClient(OpenAICompatibleClient):
    def __init__(self, api_key: str, base_url: str, model: str):
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            model=model,
            request_options=request_options_for_provider("deepseek"),
        )
