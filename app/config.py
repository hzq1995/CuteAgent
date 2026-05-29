from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    app_password: str = Field(..., alias="APP_PASSWORD")
    secret_key: str = Field(..., alias="SECRET_KEY")
    deepseek_api_key: str = Field("", alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field("https://api.deepseek.com", alias="DEEPSEEK_BASE_URL")
    deepseek_model: str = Field("deepseek-v4-flash", alias="DEEPSEEK_MODEL")
    mimo_api_key: str = Field("", alias="MIMO_API_KEY")
    mimo_base_url: str = Field("https://api.xiaomimimo.com/v1", alias="MIMO_BASE_URL")
    dingtalk_webhook_url: str = Field("", alias="DINGTALK_WEBHOOK_URL")
    dingtalk_access_token: str = Field("", alias="DINGTALK_ACCESS_TOKEN")
    dingtalk_public_base_url: str = Field("https://tenzi.store:7997/", alias="DINGTALK_PUBLIC_BASE_URL")


@lru_cache
def get_settings() -> Settings:
    return Settings()
