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
        trains = []
        start = datetime.strptime(req.time[:4], "%H%M")
        for i in range(10):
            dep = start + timedelta(minutes=30 * i)
            if dep.day != start.day:
                break
            arr = dep + timedelta(hours=2, minutes=40)
            trains.append(
                TrainInfo(
                    train_no=f"{101 + i * 2:03d}",
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
