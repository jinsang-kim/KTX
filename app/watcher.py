"""빈자리 감시 → 자동 예약 루프."""

import asyncio
import logging
import random
import uuid
from datetime import datetime

from app.config import Settings
from app.models import SearchRequest, Watch, WatchCreate, WatchStatus
from app.notifier import Notifier
from app.providers import ProviderError, SoldOutError, TrainProvider

log = logging.getLogger(__name__)

# 동시에 돌릴 수 있는 감시 작업 수. 한 계정으로 과도한 요청을 보내지 않도록 제한
MAX_ACTIVE_WATCHES = 3


class WatchLimitError(Exception):
    pass


class WatchManager:
    def __init__(self, provider: TrainProvider, notifier: Notifier, settings: Settings):
        self.provider = provider
        self.notifier = notifier
        self.settings = settings
        # TODO: 서버 재시작 시 유지되도록 SQLite 등으로 영속화
        self.watches: dict[str, Watch] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    # ---- 조회 ----
    def list(self) -> list[Watch]:
        return sorted(self.watches.values(), key=lambda w: w.created_at, reverse=True)

    def get(self, watch_id: str) -> Watch | None:
        return self.watches.get(watch_id)

    # ---- 생성/취소 ----
    def create(self, data: WatchCreate) -> Watch:
        active = sum(1 for w in self.watches.values() if w.status == WatchStatus.WATCHING)
        if active >= MAX_ACTIVE_WATCHES:
            raise WatchLimitError(f"동시에 최대 {MAX_ACTIVE_WATCHES}개까지 감시할 수 있습니다.")
        watch = Watch(id=uuid.uuid4().hex[:8], created_at=datetime.now(), **data.model_dump())
        self.watches[watch.id] = watch
        self._tasks[watch.id] = asyncio.create_task(self._run(watch), name=f"watch-{watch.id}")
        return watch

    def cancel(self, watch_id: str) -> Watch | None:
        watch = self.watches.get(watch_id)
        if not watch:
            return None
        if watch.status == WatchStatus.WATCHING:
            watch.status = WatchStatus.CANCELLED
        task = self._tasks.pop(watch_id, None)
        if task:
            task.cancel()
        return watch

    async def shutdown(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()

    # ---- 감시 루프 ----
    async def _run(self, watch: Watch) -> None:
        try:
            while watch.status == WatchStatus.WATCHING:
                await self.check_once(watch)
                if watch.status != WatchStatus.WATCHING:
                    break
                await asyncio.sleep(self._next_delay(watch))
        except asyncio.CancelledError:
            pass
        except Exception as e:  # 예상치 못한 버그로 루프가 조용히 죽지 않도록
            log.exception("watch %s crashed", watch.id)
            watch.status = WatchStatus.FAILED
            watch.last_error = f"내부 오류: {e}"
        finally:
            self._tasks.pop(watch.id, None)

    def _next_delay(self, watch: Watch) -> float:
        base = self.settings.poll_interval_sec
        # 오류가 이어지면 지수 백오프 (최대 5분)
        if watch.consecutive_errors:
            base = min(base * 2 ** watch.consecutive_errors, 300)
        # 일정한 주기로 찍히지 않도록 ±20% 지터
        return base * random.uniform(0.8, 1.2)

    async def check_once(self, watch: Watch) -> None:
        """한 번 조회하고, 조건에 맞는 자리가 있으면 예약까지 시도한다."""
        if self._is_departed(watch):
            watch.status = WatchStatus.EXPIRED
            return

        watch.attempts += 1
        watch.last_checked_at = datetime.now()
        req = SearchRequest(dep=watch.dep, arr=watch.arr, date=watch.date, time=watch.search_time)

        try:
            trains = await self.provider.search(req, watch.passengers)
        except ProviderError as e:
            self._record_error(watch, e)
            return

        watch.consecutive_errors = 0
        watch.last_error = None

        by_no = {t.train_no: t for t in trains}
        # 사용자가 지정한 우선순위대로 시도
        candidates = [by_no[n] for n in watch.train_nos if n in by_no and by_no[n].is_available(watch.seat_option)]

        for train in candidates:
            try:
                rsv = await self.provider.reserve(train, watch.seat_option, watch.passengers)
            except SoldOutError as e:
                log.info("watch %s: %s", watch.id, e)
                continue
            except ProviderError as e:
                self._record_error(watch, e)
                return

            watch.reservation = rsv
            watch.status = WatchStatus.RESERVED
            await self.notifier.send(
                f"🚄 KTX 예약 완료!\n"
                f"{rsv.dep_name}→{rsv.arr_name} {rsv.dep_date} {rsv.dep_time[:2]}:{rsv.dep_time[2:4]} "
                f"({rsv.train_no}, {rsv.seat_class})\n"
                f"예약번호 {rsv.rsv_id}\n"
                f"⏰ 결제 기한: {rsv.payment_deadline or '코레일톡에서 확인'} — 기한 내 결제하지 않으면 자동 취소됩니다."
            )
            return

    def _record_error(self, watch: Watch, e: Exception) -> None:
        watch.consecutive_errors += 1
        watch.last_error = str(e)
        log.warning("watch %s error (%d): %s", watch.id, watch.consecutive_errors, e)
        if watch.consecutive_errors >= self.settings.max_consecutive_errors:
            watch.status = WatchStatus.FAILED
            asyncio.create_task(self.notifier.send(f"⚠️ 감시 {watch.id} 중단: 오류 반복 ({e})"))

    def _is_departed(self, watch: Watch) -> bool:
        # 감시 대상 열차 중 가장 늦은 출발 시각을 알 수 없으므로 날짜 기준으로만 만료 판단
        # TODO: 감시 생성 시 열차별 출발 시각을 저장해 더 정확하게 만료 처리
        return datetime.now().strftime("%Y%m%d") > watch.date
