"""예약 성공/실패 알림. 설정된 채널(카카오톡·Gmail·텔레그램) 모두로 보낸다.
(웹 UI는 /api/watches 폴링 + 브라우저 Notification 으로 별도 알림)"""

import asyncio
import json
import logging
import smtplib
import time
from abc import ABC, abstractmethod
from email.message import EmailMessage
from pathlib import Path

import httpx

from app.config import Settings

log = logging.getLogger(__name__)


class Channel(ABC):
    name: str

    @abstractmethod
    def enabled(self) -> bool: ...

    @abstractmethod
    async def send(self, text: str) -> None: ...


class TelegramChannel(Channel):
    name = "telegram"

    def __init__(self, settings: Settings):
        self.token = settings.telegram_bot_token
        self.chat_id = settings.telegram_chat_id

    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    async def send(self, text: str) -> None:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text},
            )
            r.raise_for_status()


class EmailChannel(Channel):
    """Gmail SMTP. 구글 계정 2단계 인증을 켜고 '앱 비밀번호'를 발급받아 사용한다."""

    name = "email"

    def __init__(self, settings: Settings):
        self.address = settings.gmail_address
        self.app_password = settings.gmail_app_password.replace(" ", "")
        self.to = settings.notify_email_to or settings.gmail_address

    def enabled(self) -> bool:
        return bool(self.address and self.app_password)

    async def send(self, text: str) -> None:
        msg = EmailMessage()
        msg["Subject"] = text.splitlines()[0]
        msg["From"] = self.address
        msg["To"] = self.to
        msg.set_content(text)
        await asyncio.to_thread(self._send_sync, msg)

    def _send_sync(self, msg: EmailMessage) -> None:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as smtp:
            smtp.starttls()
            smtp.login(self.address, self.app_password)
            smtp.send_message(msg)


class KakaoChannel(Channel):
    """카카오톡 '나에게 보내기'. 최초 1회 웹 UI에서 카카오 로그인해 토큰을 받아야 한다.
    access token(약 6시간)은 refresh token(약 2개월)으로 자동 갱신하며 파일에 저장한다."""

    name = "kakao"
    AUTH_URL = "https://kauth.kakao.com/oauth/authorize"
    TOKEN_URL = "https://kauth.kakao.com/oauth/token"
    MEMO_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"

    def __init__(self, settings: Settings):
        self.rest_api_key = settings.kakao_rest_api_key
        self.client_secret = settings.kakao_client_secret
        self.redirect_uri = settings.kakao_redirect_uri
        self.app_url = settings.app_url
        self.token_file = Path(settings.data_dir) / "kakao_token.json"
        self._lock = asyncio.Lock()

    def enabled(self) -> bool:
        return bool(self.rest_api_key) and self.token_file.exists()

    def configured(self) -> bool:
        return bool(self.rest_api_key)

    # ---- OAuth ----
    def authorize_url(self, state: str) -> str:
        q = httpx.QueryParams(
            client_id=self.rest_api_key,
            redirect_uri=self.redirect_uri,
            response_type="code",
            scope="talk_message",
            state=state,
        )
        return f"{self.AUTH_URL}?{q}"

    async def exchange_code(self, code: str) -> None:
        data = {
            "grant_type": "authorization_code",
            "client_id": self.rest_api_key,
            "redirect_uri": self.redirect_uri,
            "code": code,
        }
        self._save(await self._token_request(data))

    async def _token_request(self, data: dict) -> dict:
        if self.client_secret:
            data["client_secret"] = self.client_secret
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(self.TOKEN_URL, data=data)
        if r.is_error:
            raise RuntimeError(f"카카오 토큰 요청 실패: {r.text}")
        return r.json()

    def _load(self) -> dict:
        return json.loads(self.token_file.read_text())

    def _save(self, tok: dict) -> None:
        old = self._load() if self.token_file.exists() else {}
        merged = {**old, **tok}  # refresh 응답엔 refresh_token 이 없을 수 있어 기존 값 유지
        merged["expires_at"] = time.time() + tok.get("expires_in", 0) - 60
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        self.token_file.write_text(json.dumps(merged))

    def disconnect(self) -> None:
        self.token_file.unlink(missing_ok=True)

    async def _access_token(self) -> str:
        async with self._lock:
            tok = self._load()
            if time.time() >= tok.get("expires_at", 0):
                self._save(await self._token_request(
                    {"grant_type": "refresh_token", "client_id": self.rest_api_key,
                     "refresh_token": tok["refresh_token"]}
                ))
                tok = self._load()
            return tok["access_token"]

    # ---- 전송 ----
    async def send(self, text: str) -> None:
        template = {
            "object_type": "text",
            "text": text[:200],  # 카카오 텍스트 템플릿 최대 200자
            "link": {"web_url": self.app_url, "mobile_web_url": self.app_url},
            "button_title": "앱 열기",
        }
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                self.MEMO_URL,
                headers={"Authorization": f"Bearer {await self._access_token()}"},
                data={"template_object": json.dumps(template, ensure_ascii=False)},
            )
        if r.is_error:
            raise RuntimeError(f"카카오톡 전송 실패: {r.text}")


class Notifier:
    def __init__(self, settings: Settings):
        self.kakao = KakaoChannel(settings)
        self.channels: list[Channel] = [self.kakao, EmailChannel(settings), TelegramChannel(settings)]

    def status(self) -> dict[str, bool]:
        return {c.name: c.enabled() for c in self.channels}

    async def send(self, text: str) -> dict[str, str]:
        """활성화된 모든 채널로 전송. 한 채널이 실패해도 나머지는 계속 보낸다."""
        log.info("[notify] %s", text)
        results: dict[str, str] = {}
        for c in self.channels:
            if not c.enabled():
                continue
            try:
                await c.send(text)
                results[c.name] = "ok"
            except Exception as e:
                log.exception("%s 알림 전송 실패", c.name)
                results[c.name] = f"error: {e}"
        return results
