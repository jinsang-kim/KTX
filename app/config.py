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

    # 이 앱의 주소. 카카오 메시지의 '앱 열기' 링크 등에 쓰임
    app_url: str = "http://localhost:8000"
    # 카카오 토큰 등 런타임 데이터 저장 위치 (git 에 올리지 않음)
    data_dir: str = "data"

    # 알림: 카카오톡 '나에게 보내기'
    kakao_rest_api_key: str = ""
    kakao_client_secret: str = ""
    kakao_redirect_uri: str = "http://localhost:8000/api/kakao/callback"

    # 알림: Gmail (앱 비밀번호 필요)
    gmail_address: str = ""
    gmail_app_password: str = ""
    notify_email_to: str = ""  # 비우면 gmail_address 로 보냄

    # 알림: 텔레그램
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    @field_validator("poll_interval_sec")
    @classmethod
    def _clamp_interval(cls, v: float) -> float:
        return max(v, MIN_POLL_INTERVAL_SEC)


@lru_cache
def get_settings() -> Settings:
    return Settings()
