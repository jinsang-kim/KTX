from datetime import datetime, timedelta

import pytest

from app.config import Settings
from app.models import ReservationInfo, TrainInfo, Watch, WatchCreate, WatchStatus
from app.notifier import Notifier
from app.providers import ProviderError, SoldOutError, TrainProvider
from app.watcher import WatchManager

TOMORROW = (datetime.now() + timedelta(days=1)).strftime("%Y%m%d")


def train(no: str, general=False, special=False) -> TrainInfo:
    return TrainInfo(
        train_no=no, train_type_name="KTX", dep_name="서울", arr_name="부산",
        dep_date=TOMORROW, dep_time="090000", arr_time="113000",
        general_available=general, special_available=special,
    )


class FakeProvider(TrainProvider):
    def __init__(self, trains=None, sold_out=(), search_error=None):
        self.trains = trains or []
        self.sold_out = set(sold_out)
        self.search_error = search_error
        self.reserved: list[str] = []

    async def search(self, req, passengers):
        if self.search_error:
            raise self.search_error
        return self.trains

    async def reserve(self, t, option, passengers):
        if t.train_no in self.sold_out:
            raise SoldOutError("sold out")
        self.reserved.append(t.train_no)
        return ReservationInfo(
            rsv_id="R1", train_no=t.train_no, dep_name=t.dep_name, arr_name=t.arr_name,
            dep_date=t.dep_date, dep_time=t.dep_time, seat_class="일반실",
        )


def make(provider, **settings):
    s = Settings(_env_file=None, **settings)
    return WatchManager(provider, Notifier(s), s)


def new_watch(mgr, train_nos, option="GENERAL_FIRST", date=TOMORROW):
    data = WatchCreate(dep="서울", arr="부산", date=date, train_nos=train_nos, seat_option=option)
    w = Watch(id="w1", created_at=datetime.now(), **data.model_dump())
    mgr.watches[w.id] = w
    return w


@pytest.mark.asyncio
async def test_keeps_watching_when_sold_out():
    mgr = make(FakeProvider([train("101"), train("103")]))
    w = new_watch(mgr, ["101", "103"])
    await mgr.check_once(w)
    assert w.status == WatchStatus.WATCHING
    assert w.attempts == 1


@pytest.mark.asyncio
async def test_reserves_in_priority_order():
    p = FakeProvider([train("101", general=True), train("103", general=True)])
    mgr = make(p)
    w = new_watch(mgr, ["103", "101"])
    await mgr.check_once(w)
    assert w.status == WatchStatus.RESERVED
    assert p.reserved == ["103"]


@pytest.mark.asyncio
async def test_falls_through_to_next_train_when_snatched():
    p = FakeProvider([train("101", general=True), train("103", general=True)], sold_out={"101"})
    mgr = make(p)
    w = new_watch(mgr, ["101", "103"])
    await mgr.check_once(w)
    assert w.reservation.train_no == "103"


@pytest.mark.asyncio
async def test_seat_option_filters_class():
    p = FakeProvider([train("101", general=True)])
    mgr = make(p)
    w = new_watch(mgr, ["101"], option="SPECIAL_ONLY")
    await mgr.check_once(w)
    assert w.status == WatchStatus.WATCHING
    assert p.reserved == []


@pytest.mark.asyncio
async def test_fails_after_repeated_errors():
    mgr = make(FakeProvider(search_error=ProviderError("boom")), max_consecutive_errors=3)
    w = new_watch(mgr, ["101"])
    for _ in range(3):
        await mgr.check_once(w)
    assert w.status == WatchStatus.FAILED
    assert w.last_error == "boom"


@pytest.mark.asyncio
async def test_expires_after_travel_date():
    mgr = make(FakeProvider())
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    w = new_watch(mgr, ["101"], date=yesterday)
    await mgr.check_once(w)
    assert w.status == WatchStatus.EXPIRED


def test_poll_interval_has_floor():
    assert Settings(_env_file=None, poll_interval_sec=1).poll_interval_sec == 10
