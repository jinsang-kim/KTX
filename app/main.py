import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
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
    app.state.manager = WatchManager(provider, Notifier(settings), settings)
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


app.mount("/api", api)
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
