from datetime import UTC, datetime, timedelta

import pytest

from apps.core.domain.events import DomainEvent, EventDispatcher
from apps.devices.domain.kinds import DeviceKind
from apps.notifications.domain.alerts import AlertKey, AlertKind, MonitoredDevice, ZoneHeating
from apps.notifications.domain.events import AlertCleared, AlertRaised
from apps.notifications.services.alert_book import AlertBook
from apps.notifications.stubs import DeviceSourceStub, ZoneSourceStub
from apps.notifications.usecases.evaluate_alerts import (
    EvaluateAlertsInputDTO,
    EvaluateAlertsOutputDTO,
    EvaluateAlertsUseCase,
)

NOW = datetime(2026, 10, 31, 17, 40, tzinfo=UTC)
NOTICED = 1000.0
READING_AGE = timedelta(minutes=11)
LAST_READING = NOW - READING_AGE
THERMOSTAT_KEY = AlertKey(kind=AlertKind.DEVICE_LOST, subject='thermostat-kitchen')
PLUG_KEY = AlertKey(kind=AlertKind.DEVICE_LOST, subject='plug-boiler-1f')
ZONE_KEY = AlertKey(kind=AlertKind.ZONE_OFF, subject='hallway')
LOST_THERMOSTAT = MonitoredDevice(
    slug='thermostat-kitchen',
    name='Кухня',
    kind=DeviceKind.THERMOSTAT,
    available=False,
    last_seen_age=READING_AGE,
)
FOUND_THERMOSTAT = MonitoredDevice(
    slug='thermostat-kitchen',
    name='Кухня',
    kind=DeviceKind.THERMOSTAT,
    available=True,
    last_seen_age=timedelta(0),
)
SILENT_THERMOSTAT = MonitoredDevice(
    slug='thermostat-kitchen',
    name='Кухня',
    kind=DeviceKind.THERMOSTAT,
    available=False,
    last_seen_age=None,
)
LOST_PLUG = MonitoredDevice(
    slug='plug-boiler-1f',
    name='Бойлер 1 этаж',
    kind=DeviceKind.PLUG,
    available=False,
    last_seen_age=READING_AGE,
)
STALE_METER = MonitoredDevice(
    slug='phase-meter',
    name='Фазометр',
    kind=DeviceKind.PHASE_METER,
    available=False,
    last_seen_age=timedelta(seconds=15),
)
METER_KEY = AlertKey(kind=AlertKind.DEVICE_LOST, subject='phase-meter')
COLD_ZONE = ZoneHeating(slug='hallway', name='Прихожая', should_heat=True, power=False)
CLOCK_STEP = timedelta(hours=3)


def _use_case(
    devices: list[MonitoredDevice],
    zones: ZoneSourceStub,
    book: AlertBook,
    dispatcher: EventDispatcher,
) -> EvaluateAlertsUseCase:
    return EvaluateAlertsUseCase(
        devices=(DeviceSourceStub(devices=devices),),
        zones=zones,
        book=book,
        dispatcher=dispatcher,
    )


def _thermostats_and_plugs_use_case(book: AlertBook, dispatcher: EventDispatcher) -> EvaluateAlertsUseCase:
    return EvaluateAlertsUseCase(
        devices=(DeviceSourceStub(devices=[LOST_THERMOSTAT]), DeviceSourceStub(devices=[LOST_PLUG])),
        zones=ZoneSourceStub(zones=[]),
        book=book,
        dispatcher=dispatcher,
    )


def _thermostat_and_meter_use_case(book: AlertBook, dispatcher: EventDispatcher) -> EvaluateAlertsUseCase:
    return EvaluateAlertsUseCase(
        devices=(DeviceSourceStub(devices=[LOST_THERMOSTAT]), DeviceSourceStub(devices=[STALE_METER])),
        zones=ZoneSourceStub(zones=[]),
        book=book,
        dispatcher=dispatcher,
    )


def _input(after: timedelta, *, clock_trusted: bool = True) -> EvaluateAlertsInputDTO:
    return EvaluateAlertsInputDTO(
        now=NOW + after,
        monotonic=NOTICED + after.total_seconds(),
        clock_trusted=clock_trusted,
    )


@pytest.fixture
def f_events() -> list[DomainEvent]:
    return []


@pytest.fixture
def f_dispatcher(f_events: list[DomainEvent]) -> EventDispatcher:
    dispatcher = EventDispatcher()
    for event_type in (AlertRaised, AlertCleared):
        dispatcher.register(event_type, f_events.append)
    return dispatcher


@pytest.fixture
def f_zones() -> ZoneSourceStub:
    return ZoneSourceStub(zones=[COLD_ZONE])


@pytest.fixture
def f_noticed_book(f_zones: ZoneSourceStub) -> AlertBook:
    book = AlertBook()
    _use_case([LOST_THERMOSTAT], f_zones, book, EventDispatcher()).execute(_input(timedelta(0)))
    return book


@pytest.fixture
def f_raised_book(f_noticed_book: AlertBook, f_zones: ZoneSourceStub) -> AlertBook:
    _use_case([LOST_THERMOSTAT], f_zones, f_noticed_book, EventDispatcher()).execute(_input(timedelta(minutes=2)))
    return f_noticed_book


@pytest.fixture
def f_silence_noticed_book() -> AlertBook:
    book = AlertBook()
    _use_case([SILENT_THERMOSTAT], ZoneSourceStub(zones=[]), book, EventDispatcher()).execute(
        _input(timedelta(0), clock_trusted=False),
    )
    return book


@pytest.fixture
def f_plug_noticed_book() -> AlertBook:
    book = AlertBook()
    _thermostats_and_plugs_use_case(book, EventDispatcher()).execute(_input(timedelta(0)))
    return book


@pytest.fixture
def f_meter_noticed_book() -> AlertBook:
    book = AlertBook()
    _thermostat_and_meter_use_case(book, EventDispatcher()).execute(_input(timedelta(0)))
    return book


@pytest.fixture
def f_lost_use_case(
    f_noticed_book: AlertBook,
    f_zones: ZoneSourceStub,
    f_dispatcher: EventDispatcher,
) -> EvaluateAlertsUseCase:
    return _use_case([LOST_THERMOSTAT], f_zones, f_noticed_book, f_dispatcher)


@pytest.fixture
def f_still_lost_use_case(
    f_raised_book: AlertBook,
    f_zones: ZoneSourceStub,
    f_dispatcher: EventDispatcher,
) -> EvaluateAlertsUseCase:
    return _use_case([LOST_THERMOSTAT], f_zones, f_raised_book, f_dispatcher)


@pytest.fixture
def f_found_use_case(
    f_raised_book: AlertBook,
    f_zones: ZoneSourceStub,
    f_dispatcher: EventDispatcher,
) -> EvaluateAlertsUseCase:
    return _use_case([FOUND_THERMOSTAT], f_zones, f_raised_book, f_dispatcher)


@pytest.fixture
def f_silent_use_case(f_silence_noticed_book: AlertBook, f_dispatcher: EventDispatcher) -> EvaluateAlertsUseCase:
    return _use_case([SILENT_THERMOSTAT], ZoneSourceStub(zones=[]), f_silence_noticed_book, f_dispatcher)


@pytest.fixture
def f_plug_lost_use_case(f_plug_noticed_book: AlertBook, f_dispatcher: EventDispatcher) -> EvaluateAlertsUseCase:
    return _thermostats_and_plugs_use_case(f_plug_noticed_book, f_dispatcher)


@pytest.fixture
def f_meter_stale_use_case(f_meter_noticed_book: AlertBook, f_dispatcher: EventDispatcher) -> EvaluateAlertsUseCase:
    return _thermostat_and_meter_use_case(f_meter_noticed_book, f_dispatcher)


def test_confirmed_device_loss_raises_an_alert(
    f_lost_use_case: EvaluateAlertsUseCase,
    f_events: list[DomainEvent],
) -> None:
    output = f_lost_use_case.execute(_input(timedelta(minutes=2)))

    assert (output, f_events) == (
        EvaluateAlertsOutputDTO(raised=(THERMOSTAT_KEY,), cleared=()),
        [
            AlertRaised(
                kind=AlertKind.DEVICE_LOST,
                subject='thermostat-kitchen',
                name='Кухня',
                device_kind=DeviceKind.THERMOSTAT,
                since=LAST_READING,
                occurred_at=NOW + timedelta(minutes=2),
            ),
        ],
    )


def test_lost_plug_raises_the_same_alert_under_its_own_name(
    f_plug_lost_use_case: EvaluateAlertsUseCase,
    f_events: list[DomainEvent],
) -> None:
    output = f_plug_lost_use_case.execute(_input(timedelta(minutes=2)))

    assert (output.raised, f_events[-1]) == (
        (THERMOSTAT_KEY, PLUG_KEY),
        AlertRaised(
            kind=AlertKind.DEVICE_LOST,
            subject='plug-boiler-1f',
            name='Бойлер 1 этаж',
            device_kind=DeviceKind.PLUG,
            since=LAST_READING,
            occurred_at=NOW + timedelta(minutes=2),
        ),
    )


def test_stale_phase_meter_alerts_a_minute_before_a_lost_thermostat(
    f_meter_stale_use_case: EvaluateAlertsUseCase,
    f_events: list[DomainEvent],
) -> None:
    output = f_meter_stale_use_case.execute(_input(timedelta(minutes=1)))

    assert (output.raised, f_events) == (
        (METER_KEY,),
        [
            AlertRaised(
                kind=AlertKind.DEVICE_LOST,
                subject='phase-meter',
                name='Фазометр',
                device_kind=DeviceKind.PHASE_METER,
                since=NOW - timedelta(seconds=15),
                occurred_at=NOW + timedelta(minutes=1),
            ),
        ],
    )


def test_zone_off_for_thirty_five_minutes_raises_an_alert(
    f_lost_use_case: EvaluateAlertsUseCase,
    f_events: list[DomainEvent],
) -> None:
    output = f_lost_use_case.execute(_input(timedelta(minutes=35)))

    assert (output.raised, f_events[-1]) == (
        (THERMOSTAT_KEY, ZONE_KEY),
        AlertRaised(
            kind=AlertKind.ZONE_OFF,
            subject='hallway',
            name='Прихожая',
            device_kind=DeviceKind.THERMOSTAT,
            since=NOW,
            occurred_at=NOW + timedelta(minutes=35),
        ),
    )


def test_raised_alert_is_not_repeated(
    f_still_lost_use_case: EvaluateAlertsUseCase,
    f_events: list[DomainEvent],
) -> None:
    output = f_still_lost_use_case.execute(_input(timedelta(minutes=10)))

    assert (output, f_events) == (EvaluateAlertsOutputDTO(raised=(), cleared=()), [])


def test_device_reading_again_clears_its_alert(
    f_found_use_case: EvaluateAlertsUseCase,
    f_events: list[DomainEvent],
) -> None:
    output = f_found_use_case.execute(_input(timedelta(minutes=5)))

    assert (output, f_events) == (
        EvaluateAlertsOutputDTO(raised=(), cleared=(THERMOSTAT_KEY,)),
        [
            AlertCleared(
                kind=AlertKind.DEVICE_LOST,
                subject='thermostat-kitchen',
                name='Кухня',
                device_kind=DeviceKind.THERMOSTAT,
                since=LAST_READING,
                occurred_at=NOW + timedelta(minutes=5),
            ),
        ],
    )


def test_loss_noticed_before_a_clock_step_is_dated_by_the_monotonic_clock(
    f_silent_use_case: EvaluateAlertsUseCase,
    f_events: list[DomainEvent],
) -> None:
    f_silent_use_case.execute(
        EvaluateAlertsInputDTO(
            now=NOW + CLOCK_STEP + timedelta(minutes=10),
            monotonic=NOTICED + 600,
            clock_trusted=True,
        ),
    )

    assert f_events == [
        AlertRaised(
            kind=AlertKind.DEVICE_LOST,
            subject='thermostat-kitchen',
            name='Кухня',
            device_kind=DeviceKind.THERMOSTAT,
            since=NOW + CLOCK_STEP,
            occurred_at=NOW + CLOCK_STEP + timedelta(minutes=10),
        ),
    ]


def test_alert_raised_while_the_clock_is_untrusted_names_no_time(
    f_silent_use_case: EvaluateAlertsUseCase,
    f_events: list[DomainEvent],
) -> None:
    f_silent_use_case.execute(_input(timedelta(minutes=10), clock_trusted=False))

    assert f_events == [
        AlertRaised(
            kind=AlertKind.DEVICE_LOST,
            subject='thermostat-kitchen',
            name='Кухня',
            device_kind=DeviceKind.THERMOSTAT,
            since=None,
            occurred_at=NOW + timedelta(minutes=10),
        ),
    ]


def test_zone_targets_are_asked_with_the_trust_of_the_clock(
    f_lost_use_case: EvaluateAlertsUseCase,
    f_zones: ZoneSourceStub,
) -> None:
    f_lost_use_case.execute(_input(timedelta(minutes=1), clock_trusted=False))

    assert f_zones.clock_trusted == [True, False]
