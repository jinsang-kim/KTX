from abc import ABC, abstractmethod

from app.models import Passengers, ReservationInfo, SearchRequest, SeatOption, TrainInfo


class ProviderError(Exception):
    """조회/예약 중 일시적인 오류 (네트워크, 세션 만료 등). 재시도 대상."""


class SoldOutError(ProviderError):
    """조회 시점엔 자리가 있었지만 예약 시점에 매진된 경우."""


class TrainProvider(ABC):
    """열차 조회/예약 백엔드. mock ↔ 실제 코레일을 교체할 수 있도록 추상화."""

    @abstractmethod
    async def search(self, req: SearchRequest, passengers: Passengers) -> list[TrainInfo]: ...

    @abstractmethod
    async def reserve(
        self, train: TrainInfo, option: SeatOption, passengers: Passengers
    ) -> ReservationInfo: ...

    async def close(self) -> None:
        pass
