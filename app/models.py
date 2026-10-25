from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, PrivateAttr

SeatOption = Literal["GENERAL_FIRST", "GENERAL_ONLY", "SPECIAL_FIRST", "SPECIAL_ONLY"]


class TrainInfo(BaseModel):
    train_no: str
    train_type_name: str
    dep_name: str
    arr_name: str
    dep_date: str  # YYYYMMDD
    dep_time: str  # HHMMSS
    arr_time: str  # HHMMSS
    general_available: bool
    special_available: bool

    # provider 내부 객체(예: korail2.Train). 예약 시 그대로 넘기기 위해 보관
    _raw: Any = PrivateAttr(default=None)

    def is_available(self, option: SeatOption) -> bool:
        if option == "GENERAL_ONLY":
            return self.general_available
        if option == "SPECIAL_ONLY":
            return self.special_available
        return self.general_available or self.special_available


class SearchRequest(BaseModel):
    dep: str = Field(examples=["서울"])
    arr: str = Field(examples=["부산"])
    date: str = Field(pattern=r"^\d{8}$", examples=["20261101"])
    time: str = Field(default="000000", pattern=r"^\d{6}$")


class Passengers(BaseModel):
    adults: int = Field(default=1, ge=0, le=9)
    children: int = Field(default=0, ge=0, le=9)
    seniors: int = Field(default=0, ge=0, le=9)

    @property
    def total(self) -> int:
        return self.adults + self.children + self.seniors


class WatchCreate(BaseModel):
    dep: str
    arr: str
    date: str = Field(pattern=r"^\d{8}$")
    # 감시할 열차 번호. 앞에 있을수록 우선 예약
    train_nos: list[str] = Field(min_length=1, max_length=10)
    # 조회 시작 시각. 코레일은 한 번에 약 10개 열차만 돌려주므로
    # 감시 대상 중 가장 이른 출발 시각으로 맞춰야 대상 열차가 조회 결과에 포함된다
    search_time: str = Field(default="000000", pattern=r"^\d{6}$")
    seat_option: SeatOption = "GENERAL_FIRST"
    passengers: Passengers = Passengers()


class WatchStatus(str, Enum):
    WATCHING = "watching"
    RESERVED = "reserved"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class ReservationInfo(BaseModel):
    rsv_id: str
    train_no: str
    dep_name: str
    arr_name: str
    dep_date: str
    dep_time: str
    seat_class: str
    price: int | None = None
    # 결제 기한. 이 시간 안에 코레일톡/홈페이지에서 결제하지 않으면 자동 취소됨
    payment_deadline: str | None = None


class Watch(WatchCreate):
    id: str
    status: WatchStatus = WatchStatus.WATCHING
    created_at: datetime
    last_checked_at: datetime | None = None
    attempts: int = 0
    consecutive_errors: int = 0
    last_error: str | None = None
    reservation: ReservationInfo | None = None
