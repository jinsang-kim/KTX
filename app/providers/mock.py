"""개발용 가짜 provider. 실제 코레일 서버에 요청하지 않는다."""

import random
import uuid
from datetime import datetime, timedelta

from app.models import Passengers, ReservationInfo, SearchRequest, SeatOption, TrainInfo
from app.providers.base import SoldOutError, TrainProvider


class MockProvider(TrainProvider):
    def __init__(self, availability: float = 0.15, sold_out_on_reserve: float = 0.2, seed: int | None = None):
        # availability: 조회마다 매진 열차에 자리가 날 확률
        # sold_out_on_reserve: 자리를 봤는데 예약 직전에 뺏길 확률
        self.availability = availability
        self.sold_out_on_reserve = sold_out_on_reserve
        self.rng = random.Random(seed)

    async def search(self, req: SearchRequest, passengers: Passengers) -> list[TrainInfo]:
        # 05:00부터 30분 간격으로 고정 시간표를 만든다. 실제 코레일처럼 열차 번호가
        # 조회 시각과 무관하게 항상 같은 열차를 가리켜야 감시 로직을 제대로 검증할 수 있다
        first = datetime.strptime("0500", "%H%M")
        req_time = datetime.strptime(req.time[:4], "%H%M")
        trains = []
        for slot in range(38):  # 05:00 ~ 23:30
            dep = first + timedelta(minutes=30 * slot)
            if dep < req_time:
                continue
            if len(trains) == 10:  # 코레일처럼 한 번에 최대 10개
                break
            arr = dep + timedelta(hours=2, minutes=40)
            trains.append(
                TrainInfo(
                    train_no=f"{101 + slot * 2:03d}",
                    train_type_name="KTX",
                    dep_name=req.dep,
                    arr_name=req.arr,
                    dep_date=req.date,
                    dep_time=dep.strftime("%H%M00"),
                    arr_time=arr.strftime("%H%M00"),
                    general_available=self.rng.random() < self.availability,
                    special_available=self.rng.random() < self.availability / 2,
                )
            )
        return trains

    async def reserve(self, train: TrainInfo, option: SeatOption, passengers: Passengers) -> ReservationInfo:
        if self.rng.random() < self.sold_out_on_reserve:
            raise SoldOutError(f"{train.train_no} 열차 예약 직전 매진")
        seat_class = "일반실" if train.general_available and option != "SPECIAL_ONLY" else "특실"
        if option == "SPECIAL_FIRST" and train.special_available:
            seat_class = "특실"
        deadline = datetime.now() + timedelta(minutes=10)
        return ReservationInfo(
            rsv_id=uuid.uuid4().hex[:10].upper(),
            train_no=train.train_no,
            dep_name=train.dep_name,
            arr_name=train.arr_name,
            dep_date=train.dep_date,
            dep_time=train.dep_time,
            seat_class=seat_class,
            price=59800 * passengers.total,
            payment_deadline=deadline.strftime("%Y-%m-%d %H:%M"),
        )
