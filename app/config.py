from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_POLL_INTERVAL_SEC = 10


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    provider: Literal["mock", "korail"] = "mock"
    korail_id: str = ""
    korail_pw: str = ""

    poll_interval_sec: float = 20
    # 연속 오류가 이 횟수를 넘으면 감시를 실패 처리
    max_consecutive_errors: int = 10

    app_token: str = ""

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    @field_validator("poll_interval_sec")
    @classmethod
    def _clamp_interval(cls, v: float) -> float:
        return max(v, MIN_POLL_INTERVAL_SEC)


@lru_cache
def get_settings() -> Settings:
    return Settings()
