from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.core.domain.events import DomainEvent, EventDispatcher
from apps.core.stubs import MonotonicClockStub
from apps.devices.domain.events import DeviceAvailabilityChanged
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.domain.readings import Phase, TemperatureReading
from apps.devices.factories import DeviceFactory
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.phase_meter_store import PhaseMeterStore
from apps.devices.services.state_store import TemperatureStateStore
from apps.devices.usecases.check_sensors import CheckSensorsInputDTO, CheckSensorsOutputDTO, CheckSensorsUseCase

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
LATER = NOW + timedelta(minutes=10, seconds=30)
READING = TemperatureReading(temperature=Decimal('21.5'), humidity=Decimal('41.2'), read_at=NOW)


@pytest.fixture
def clock() -> MonotonicClockStub:
    return MonotonicClockStub(now=1000.0)


@pytest.fixture
def temperature_store(clock: MonotonicClockStub) -> TemperatureStateStore:
    store = TemperatureStateStore(unavailable_after=timedelta(minutes=10), clock=clock)
    store.record_state('air-kitchen', READING)
    store.record_failure('outdoor', error_code='broker_unavailable', error='mqtt broker unreachable')
    return store


@pytest.fixture
def phase_meter_store(clock: MonotonicClockStub) -> PhaseMeterStore:
    store = PhaseMeterStore(stale_after=timedelta(seconds=10), unavailable_after=timedelta(minutes=10), clock=clock)
    for phase, amps in ((Phase.A, '12.34'), (Phase.B, '4.10'), (Phase.C, '0.52')):
        store.record_current('phase-meter', phase, Decimal(amps), NOW)
    return store


@pytest.fixture
def f_events() -> list[DomainEvent]:
    return []


@pytest.fixture
def dispatcher(f_events: list[DomainEvent]) -> EventDispatcher:
    event_dispatcher = EventDispatcher()
    event_dispatcher.register(DeviceAvailabilityChanged, f_events.append)
    return event_dispatcher


@pytest.fixture
def f_use_case(
    temperature_store: TemperatureStateStore,
    phase_meter_store: PhaseMeterStore,
    dispatcher: EventDispatcher,
) -> CheckSensorsUseCase:
    for slug in ('outdoor', 'air-kitchen', 'air-hallway'):
        DeviceFactory(slug=slug, kind=DeviceKind.TEMPERATURE_SENSOR, driver=DeviceDriver.ESPHOME_TEMPERATURE)
    DeviceFactory(slug='phase-meter', kind=DeviceKind.PHASE_METER, driver=DeviceDriver.ESPHOME_PHASE_METER)
    DeviceFactory(slug='thermostat-kitchen')
    return CheckSensorsUseCase(
        devices=DeviceRepository(),
        stores={DeviceKind.TEMPERATURE_SENSOR: temperature_store, DeviceKind.PHASE_METER: phase_meter_store},
        dispatcher=dispatcher,
    )


@pytest.fixture
def reported_use_case(
    f_use_case: CheckSensorsUseCase,
    f_events: list[DomainEvent],
) -> CheckSensorsUseCase:
    f_use_case.execute(CheckSensorsInputDTO(now=NOW))
    f_events.clear()
    return f_use_case


@pytest.fixture
def f_silent_use_case(
    reported_use_case: CheckSensorsUseCase,
    temperature_store: TemperatureStateStore,
    clock: MonotonicClockStub,
) -> CheckSensorsUseCase:
    temperature_store.record_failure('air-kitchen', error_code='device_unavailable', error='the node went offline')
    clock.advance(10 * 60 + 1)
    return reported_use_case


@pytest.fixture
def f_stale_use_case(reported_use_case: CheckSensorsUseCase, clock: MonotonicClockStub) -> CheckSensorsUseCase:
    clock.advance(30)
    return reported_use_case


@pytest.fixture
def f_heard_again_use_case(
    reported_use_case: CheckSensorsUseCase,
    temperature_store: TemperatureStateStore,
) -> CheckSensorsUseCase:
    temperature_store.record_state('outdoor', READING)
    return reported_use_case


def test_first_check_reports_every_sensor_once(f_use_case: CheckSensorsUseCase, f_events: list[DomainEvent]) -> None:
    output = f_use_case.execute(CheckSensorsInputDTO(now=NOW))
    f_use_case.execute(CheckSensorsInputDTO(now=NOW + timedelta(seconds=30)))

    assert (output, f_events) == (
        CheckSensorsOutputDTO(available=('air-kitchen', 'phase-meter'), unavailable=('air-hallway', 'outdoor')),
        [
            DeviceAvailabilityChanged(
                slug='air-hallway',
                available=False,
                reason='no reading',
                error_code='device_unavailable',
                occurred_at=NOW,
            ),
            DeviceAvailabilityChanged(
                slug='air-kitchen',
                available=True,
                reason='reading received',
                error_code=None,
                occurred_at=NOW,
            ),
            DeviceAvailabilityChanged(
                slug='outdoor',
                available=False,
                reason='mqtt broker unreachable',
                error_code='broker_unavailable',
                occurred_at=NOW,
            ),
            DeviceAvailabilityChanged(
                slug='phase-meter',
                available=True,
                reason='reading received',
                error_code=None,
                occurred_at=NOW,
            ),
        ],
    )


def test_sensor_silent_for_10_minutes_is_reported_unavailable_with_its_failure(
    f_silent_use_case: CheckSensorsUseCase,
    f_events: list[DomainEvent],
) -> None:
    f_silent_use_case.execute(CheckSensorsInputDTO(now=LATER))

    assert f_events == [
        DeviceAvailabilityChanged(
            slug='air-kitchen',
            available=False,
            reason='the node went offline',
            error_code='device_unavailable',
            occurred_at=LATER,
        ),
        DeviceAvailabilityChanged(
            slug='phase-meter',
            available=False,
            reason='no reading',
            error_code='device_unavailable',
            occurred_at=LATER,
        ),
    ]


def test_stale_phase_meter_is_still_available(
    f_stale_use_case: CheckSensorsUseCase,
    f_events: list[DomainEvent],
) -> None:
    output = f_stale_use_case.execute(CheckSensorsInputDTO(now=NOW + timedelta(seconds=30)))

    assert (output.available, f_events) == (('air-kitchen', 'phase-meter'), [])


def test_sensor_heard_again_is_reported_available(
    f_heard_again_use_case: CheckSensorsUseCase,
    f_events: list[DomainEvent],
) -> None:
    f_heard_again_use_case.execute(CheckSensorsInputDTO(now=NOW + timedelta(seconds=30)))

    assert f_events == [
        DeviceAvailabilityChanged(
            slug='outdoor',
            available=True,
            reason='reading received',
            error_code=None,
            occurred_at=NOW + timedelta(seconds=30),
        ),
    ]
