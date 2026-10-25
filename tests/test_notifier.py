import json
import time
from urllib.parse import parse_qs

import httpx
import pytest

from app import notifier as notifier_mod
from app.config import Settings
from app.notifier import Channel, EmailChannel, KakaoChannel, Notifier


@pytest.fixture
def kakao_server(monkeypatch):
    """카카오 API 대신 응답하는 가짜 서버. 받은 요청을 기록한다."""
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
        calls.append((str(req.url), req.headers.get("authorization"), form))
        if req.url.path == "/oauth/token":
            if form["grant_type"] == "authorization_code":
                return httpx.Response(200, json={"access_token": "A1", "refresh_token": "R1", "expires_in": 21599})
            # refresh 응답에는 refresh_token 이 빠질 수 있음
            return httpx.Response(200, json={"access_token": "A2", "expires_in": 21599})
        if req.url.path == "/v2/api/talk/memo/default/send":
            return httpx.Response(200, json={"result_code": 0})
        return httpx.Response(404)

    real = httpx.AsyncClient
    monkeypatch.setattr(
        notifier_mod.httpx, "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    return calls


def kakao_settings(tmp_path, **kw):
    return Settings(_env_file=None, kakao_rest_api_key="KEY", data_dir=str(tmp_path), **kw)


@pytest.mark.asyncio
async def test_kakao_disabled_until_login(tmp_path, kakao_server):
    k = KakaoChannel(kakao_settings(tmp_path))
    assert k.configured() and not k.enabled()
    await k.exchange_code("CODE")
    assert k.enabled()
    _, _, form = kakao_server[0]
    assert form["code"] == "CODE" and form["client_id"] == "KEY"


@pytest.mark.asyncio
async def test_kakao_send_memo(tmp_path, kakao_server):
    k = KakaoChannel(kakao_settings(tmp_path))
    await k.exchange_code("CODE")
    await k.send("🚄 예약 완료" + "x" * 300)
    url, auth, form = kakao_server[-1]
    assert url.endswith("/v2/api/talk/memo/default/send")
    assert auth == "Bearer A1"
    tpl = json.loads(form["template_object"])
    assert tpl["object_type"] == "text" and len(tpl["text"]) == 200


@pytest.mark.asyncio
async def test_kakao_refreshes_expired_token_and_keeps_refresh_token(tmp_path, kakao_server):
    k = KakaoChannel(kakao_settings(tmp_path))
    await k.exchange_code("CODE")
    tok = json.loads(k.token_file.read_text())
    tok["expires_at"] = time.time() - 1
    k.token_file.write_text(json.dumps(tok))

    await k.send("hi")
    refresh = [f for _, _, f in kakao_server if f.get("grant_type") == "refresh_token"]
    assert refresh and refresh[0]["refresh_token"] == "R1"
    assert kakao_server[-1][1] == "Bearer A2"
    assert json.loads(k.token_file.read_text())["refresh_token"] == "R1"


def test_kakao_authorize_url(tmp_path):
    url = KakaoChannel(kakao_settings(tmp_path)).authorize_url("S1")
    q = parse_qs(httpx.URL(url).query.decode())
    assert q["scope"] == ["talk_message"] and q["state"] == ["S1"] and q["client_id"] == ["KEY"]


@pytest.mark.asyncio
async def test_email_sends_via_gmail_smtp(monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            sent["host"] = (host, port)
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def starttls(self): sent["tls"] = True
        def login(self, u, p): sent["login"] = (u, p)
        def send_message(self, m): sent["msg"] = m

    monkeypatch.setattr(notifier_mod.smtplib, "SMTP", FakeSMTP)
    ch = EmailChannel(Settings(_env_file=None, gmail_address="me@gmail.com", gmail_app_password="abcd efgh ijkl mnop"))
    assert ch.enabled()
    await ch.send("🚄 예약 완료\n상세")
    assert sent["host"] == ("smtp.gmail.com", 587) and sent["tls"]
    assert sent["login"] == ("me@gmail.com", "abcdefghijklmnop")  # 앱 비밀번호 공백 제거
    assert sent["msg"]["To"] == "me@gmail.com" and sent["msg"]["Subject"] == "🚄 예약 완료"


@pytest.mark.asyncio
async def test_one_failing_channel_does_not_block_others(tmp_path):
    class Boom(Channel):
        name = "boom"
        def enabled(self): return True
        async def send(self, text): raise RuntimeError("down")

    class Ok(Channel):
        name = "ok"
        got = []
        def enabled(self): return True
        async def send(self, text): self.got.append(text)

    n = Notifier(Settings(_env_file=None, data_dir=str(tmp_path)))
    n.channels = [Boom(), Ok()]
    results = await n.send("hello")
    assert results == {"boom": "error: down", "ok": "ok"}
    assert Ok.got == ["hello"]
