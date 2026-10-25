import logging

import httpx

from app.config import Settings

log = logging.getLogger(__name__)


class Notifier:
    """예약 성공/실패를 외부로 알린다. 텔레그램 설정이 없으면 로그만 남긴다.
    (웹 UI는 /api/watches 폴링 + 브라우저 Notification 으로 별도 알림)"""

    def __init__(self, settings: Settings):
        self.token = settings.telegram_bot_token
        self.chat_id = settings.telegram_chat_id

    async def send(self, text: str) -> None:
        log.info("[notify] %s", text)
        if not (self.token and self.chat_id):
            return
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"https://api.telegram.org/bot{self.token}/sendMessage",
                    json={"chat_id": self.chat_id, "text": text},
                )
        except httpx.HTTPError:
            log.exception("텔레그램 알림 전송 실패")
