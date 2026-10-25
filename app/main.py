import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.models import Passengers, SearchRequest, TrainInfo, Watch, WatchCreate
from app.notifier import Notifier
from app.providers import ProviderError, create_provider
from app.watcher import WatchLimitError, WatchManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# 자주 쓰는 KTX 정차역 (UI 자동완성용)
STATIONS = [
    "서울", "용산", "광명", "수원", "천안아산", "오송", "대전", "김천구미", "동대구",
    "신경주", "울산(통도사)", "부산", "공주", "익산", "전주", "광주송정", "목포",
    "여수EXPO", "순천", "포항", "강릉", "청량리", "진주", "마산", "창원중앙",
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    provider = create_provider(settings)
    app.state.provider = provider
    app.state.notifier = Notifier(settings)
    app.state.manager = WatchManager(provider, app.state.notifier, settings)
    yield
    await app.state.manager.shutdown()
    await provider.close()


app = FastAPI(title="KTX 빈자리 예약 도우미", lifespan=lifespan)


def require_token(x_app_token: str = Header(default="")) -> None:
    token = get_settings().app_token
    if token and x_app_token != token:
        raise HTTPException(401, "invalid app token")


api = FastAPI(dependencies=[Depends(require_token)])


@api.get("/health")
async def health():
    return {"ok": True, "provider": get_settings().provider}


@api.get("/stations")
async def stations() -> list[str]:
    return STATIONS


@api.post("/search")
async def search(req: SearchRequest, adults: int = 1) -> list[TrainInfo]:
    try:
        return await app.state.provider.search(req, Passengers(adults=adults))
    except ProviderError as e:
        raise HTTPException(502, f"열차 조회 실패: {e}")


@api.get("/watches")
async def list_watches() -> list[Watch]:
    return app.state.manager.list()


@api.post("/watches", status_code=201)
async def create_watch(data: WatchCreate) -> Watch:
    if data.passengers.total == 0:
        raise HTTPException(422, "승객 수는 1명 이상이어야 합니다.")
    try:
        return app.state.manager.create(data)
    except WatchLimitError as e:
        raise HTTPException(429, str(e))


@api.delete("/watches/{watch_id}")
async def cancel_watch(watch_id: str) -> Watch:
    watch = app.state.manager.cancel(watch_id)
    if not watch:
        raise HTTPException(404, "not found")
    return watch


# ---- 알림 ----
@api.get("/notify/status")
async def notify_status():
    n: Notifier = app.state.notifier
    return {"channels": n.status(), "kakao_configured": n.kakao.configured()}


@api.post("/notify/test")
async def notify_test():
    results = await app.state.notifier.send("🔔 KTX 예약 도우미 테스트 알림입니다.")
    if not results:
        raise HTTPException(400, "활성화된 알림 채널이 없습니다. .env 설정을 확인하세요.")
    return results


@api.delete("/kakao")
async def kakao_disconnect():
    app.state.notifier.kakao.disconnect()
    return {"ok": True}


# 카카오 로그인은 브라우저 리다이렉트라 X-App-Token 헤더를 붙일 수 없으므로
# /api 하위 앱이 아닌 메인 앱에 등록하고 쿼리 토큰 + state 로 보호한다.
# (app.mount("/api") 보다 먼저 등록해야 우선 매칭됨)
_kakao_states: set[str] = set()


@app.get("/api/kakao/login")
async def kakao_login(token: str = ""):
    settings = get_settings()
    if settings.app_token and token != settings.app_token:
        raise HTTPException(401, "invalid app token")
    kakao = app.state.notifier.kakao
    if not kakao.configured():
        raise HTTPException(400, ".env 에 KAKAO_REST_API_KEY 를 먼저 설정하세요.")
    state = secrets.token_urlsafe(16)
    _kakao_states.add(state)
    return RedirectResponse(kakao.authorize_url(state))


@app.get("/api/kakao/callback")
async def kakao_callback(state: str = "", code: str = "", error: str = "", error_description: str = ""):
    if state not in _kakao_states:
        raise HTTPException(400, "잘못된 요청입니다 (state 불일치). 다시 연결해 주세요.")
    _kakao_states.discard(state)
    if error:
        return HTMLResponse(f"<p>카카오 연결 실패: {error_description or error}</p><a href='/'>돌아가기</a>", 400)
    try:
        await app.state.notifier.kakao.exchange_code(code)
    except RuntimeError as e:
        return HTMLResponse(f"<p>{e}</p><a href='/'>돌아가기</a>", 400)
    return RedirectResponse("/?kakao=connected")


app.mount("/api", api)
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
