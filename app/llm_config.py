from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelOption:
    id: str
    label: str


@dataclass(frozen=True)
class ProviderPreset:
    id: str
    label: str
    models: tuple[ModelOption, ...]
    default_model: str
    thinking: str


DEEPSEEK_PROVIDER = "deepseek"
MIMO_PROVIDER = "mimo"
DEFAULT_PROVIDER = DEEPSEEK_PROVIDER
DEFAULT_MODEL = "deepseek-v4-flash"

PROVIDER_PRESETS: dict[str, ProviderPreset] = {
    DEEPSEEK_PROVIDER: ProviderPreset(
        id=DEEPSEEK_PROVIDER,
        label="DeepSeek",
        default_model=DEFAULT_MODEL,
        thinking="deepseek",
        models=(
            ModelOption("deepseek-v4-flash", "DeepSeek V4 Flash"),
            ModelOption("deepseek-v4-pro", "DeepSeek V4 Pro"),
            ModelOption("deepseek-chat", "deepseek-chat (legacy)"),
            ModelOption("deepseek-reasoner", "deepseek-reasoner (legacy)"),
        ),
    ),
    MIMO_PROVIDER: ProviderPreset(
        id=MIMO_PROVIDER,
        label="Xiaomi MiMo",
        default_model="mimo-v2.5-pro",
        thinking="mimo",
        models=(
            ModelOption("mimo-v2.5-pro", "MiMo V2.5 Pro"),
            ModelOption("mimo-v2.5", "MiMo V2.5"),
            ModelOption("mimo-v2-flash", "MiMo V2 Flash"),
        ),
    ),
}


def provider_options(custom_models: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    options = [
        {
            "id": preset.id,
            "label": preset.label,
            "models": [{"id": model.id, "label": model.label} for model in preset.models],
            "default_model": preset.default_model,
        }
        for preset in PROVIDER_PRESETS.values()
    ]
    for custom_model in custom_models or []:
        provider_id = str(custom_model.get("id") or "")
        model = str(custom_model.get("model") or "")
        if not provider_id or not model:
            continue
        label = str(custom_model.get("name") or model)
        options.append(
            {
                "id": provider_id,
                "label": label,
                "models": [{"id": model, "label": model}],
                "default_model": model,
                "custom": True,
            }
        )
    return options


def normalize_provider_model(
    provider: str,
    model: str,
    custom_models: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    provider_id = provider if provider in PROVIDER_PRESETS else DEFAULT_PROVIDER
    if provider_id == DEFAULT_PROVIDER:
        for custom_model in custom_models or []:
            custom_id = str(custom_model.get("id") or "")
            custom_model_name = str(custom_model.get("model") or "")
            if provider == custom_id and custom_model_name:
                return custom_id, custom_model_name
    preset = PROVIDER_PRESETS[provider_id]
    valid_models = {option.id for option in preset.models}
    model_id = model if model in valid_models else preset.default_model
    return provider_id, model_id


def custom_model_by_provider(
    provider: str,
    custom_models: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    for custom_model in custom_models or []:
        if str(custom_model.get("id") or "") == provider:
            return custom_model
    return None


def request_options_for_provider(provider: str) -> dict[str, Any]:
    preset = PROVIDER_PRESETS.get(provider, PROVIDER_PRESETS[DEFAULT_PROVIDER])
    if preset.thinking == "deepseek":
        return {
            "reasoning_effort": "high",
            "extra_body": {"thinking": {"type": "enabled"}},
        }
    if preset.thinking == "mimo":
        return {
            "extra_body": {"thinking": {"type": "enabled"}},
        }
    return {}
