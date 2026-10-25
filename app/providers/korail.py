"""실제 코레일 연동 provider (korail2 라이브러리 래핑).

⚠️ 주의
- 코레일 공식 API가 아니라 모바일 앱 비공식 API를 사용한다. 코레일 측 변경(앱 버전 체크,
  봇 차단 등)으로 언제든 동작하지 않을 수 있으므로 실제 사용 전 반드시 검증이 필요하다.
  PyPI의 korail2 0.4.0 은 오래되어 로그인이 실패할 수 있으며, 그 경우 최근에 유지보수되는
  구현(예: SRTgo 프로젝트의 ktx 모듈)을 참고해 이 파일만 교체하면 된다.
- korail2 는 동기(blocking) 라이브러리이므로 스레드에서 실행하고, 하나의 세션을
  공유하므로 lock 으로 직렬화한다.
"""

import asyncio

from app.models import Passengers, ReservationInfo, SearchRequest, SeatOption, TrainInfo
from app.providers.base import ProviderError, SoldOutError, TrainProvider


class KorailProvider(TrainProvider):
    def __init__(self, korail_id: str, korail_pw: str):
        try:
            import korail2
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("PROVIDER=korail 을 쓰려면 `pip install korail2 pycryptodome` 이 필요합니다.") from e
        if not korail_id or not korail_pw:
            raise RuntimeError("KORAIL_ID / KORAIL_PW 환경변수를 설정하세요.")

        self._k = korail2
        self._client = korail2.Korail(korail_id, korail_pw, auto_login=False)
        self._lock = asyncio.Lock()

    async def _call(self, fn, *args, **kwargs):
        async with self._lock:
            return await asyncio.to_thread(self._call_with_relogin, fn, *args, **kwargs)

    def _call_with_relogin(self, fn, *args, **kwargs):
        k = self._k
        if not self._client.logined and not self._client.login():
            raise ProviderError("코레일 로그인 실패")
        try:
            return fn(*args, **kwargs)
        except k.NeedToLoginError:
            if not self._client.login():
                raise ProviderError("코레일 재로그인 실패")
            return fn(*args, **kwargs)
        except k.NoResultsError:
            return []
        except k.SoldOutError as e:
            raise SoldOutError(str(e)) from e
        except k.KorailError as e:
            raise ProviderError(str(e)) from e
        except Exception as e:  # 네트워크 오류 등
            raise ProviderError(f"{type(e).__name__}: {e}") from e

    def _passengers(self, p: Passengers):
        k = self._k
        result = []
        if p.adults:
            result.append(k.AdultPassenger(p.adults))
        if p.children:
            result.append(k.ChildPassenger(p.children))
        if p.seniors:
            result.append(k.SeniorPassenger(p.seniors))
        return result

    async def search(self, req: SearchRequest, passengers: Passengers) -> list[TrainInfo]:
        raw_trains = await self._call(
            self._client.search_train,
            req.dep,
            req.arr,
            req.date,
            req.time,
            train_type=self._k.TrainType.KTX,
            passengers=self._passengers(passengers),
            include_no_seats=True,  # 매진 열차도 받아야 감시 대상으로 고를 수 있음
        )
        trains = []
        for t in raw_trains:
            info = TrainInfo(
                train_no=t.train_no,
                train_type_name=t.train_type_name,
                dep_name=t.dep_name,
                arr_name=t.arr_name,
                dep_date=t.dep_date,
                dep_time=t.dep_time,
                arr_time=t.arr_time,
                general_available=t.has_general_seat(),
                special_available=t.has_special_seat(),
            )
            info._raw = t
            trains.append(info)
        return trains

    async def reserve(self, train: TrainInfo, option: SeatOption, passengers: Passengers) -> ReservationInfo:
        if train._raw is None:
            raise ProviderError("search() 로 얻은 열차만 예약할 수 있습니다.")
        rsv = await self._call(
            self._client.reserve,
            train._raw,
            passengers=self._passengers(passengers),
            option=option,
        )
        deadline = None
        if getattr(rsv, "buy_limit_date", None):
            d, t = rsv.buy_limit_date, rsv.buy_limit_time
            deadline = f"{d[:4]}-{d[4:6]}-{d[6:]} {t[:2]}:{t[2:4]}"
        return ReservationInfo(
            rsv_id=rsv.rsv_id,
            train_no=rsv.train_no,
            dep_name=rsv.dep_name,
            arr_name=rsv.arr_name,
            dep_date=rsv.dep_date,
            dep_time=rsv.dep_time,
            seat_class="특실" if option.startswith("SPECIAL") and train.special_available else "일반실",
            price=int(rsv.price) if getattr(rsv, "price", None) else None,
            payment_deadline=deadline,
        )
