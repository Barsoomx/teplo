from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.core.domain.events import DomainEvent, EventDispatcher
from apps.core.stubs import MonotonicClockStub
from apps.devices.domain.errors import DeviceUnavailableError
from apps.devices.domain.events import DeviceAvailabilityChanged
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.domain.readings import ClimateMode, ClimateUnitState, ReadingPair
from apps.devices.factories import DeviceFactory
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.state_store import ClimateUnitStateStore
from apps.devices.stubs import ChangingClimateUnitStub, ClimateUnitProviderStub, ClimateUnitStub
from apps.devices.usecases.poll_climate_units import (
    PollClimateUnitsInputDTO,
    PollClimateUnitsOutputDTO,
    PollClimateUnitsUseCase,
)

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 4, 16, 20, tzinfo=UTC)
POLL_INTERVAL = 30.0


@pytest.fixture
def state() -> ClimateUnitState:
    return ClimateUnitState(
        mode=ClimateMode.HEAT,
        target=Decimal(22),
        room_temperature=Decimal(21),
        read_at=NOW,
        level='High',
    )


@pytest.fixture
def clock() -> MonotonicClockStub:
    return MonotonicClockStub(now=0.0)


@pytest.fixture
def f_store(clock: MonotonicClockStub) -> ClimateUnitStateStore:
    return ClimateUnitStateStore(unavailable_after=timedelta(minutes=10), clock=clock)


@pytest.fixture
def f_received() -> list[DomainEvent]:
    return []


@pytest.fixture
def dispatcher(f_received: list[DomainEvent]) -> EventDispatcher:
    event_dispatcher = EventDispatcher()
    event_dispatcher.register(DeviceAvailabilityChanged, f_received.append)
    return event_dispatcher


@pytest.fixture
def f_use_case(
    state: ClimateUnitState,
    f_store: ClimateUnitStateStore,
    clock: MonotonicClockStub,
    dispatcher: EventDispatcher,
) -> PollClimateUnitsUseCase:
    DeviceFactory(slug='heater-office', kind=DeviceKind.CLIMATE_UNIT, driver=DeviceDriver.TUYA_HEATER)
    DeviceFactory(slug='heater-bedroom', kind=DeviceKind.CLIMATE_UNIT, driver=DeviceDriver.TUYA_HEATER)
    DeviceFactory(slug='thermostat-kitchen')
    provider = ClimateUnitProviderStub(
        units={
            'heater-office': ClimateUnitStub(state=state),
            'heater-bedroom': ClimateUnitStub(state=None, error=DeviceUnavailableError('timeout')),
        },
    )
    return PollClimateUnitsUseCase(
        devices=DeviceRepository(),
        provider=provider,
        store=f_store,
        clock=clock,
        dispatcher=dispatcher,
    )


@pytest.fixture
def f_changed(state: ClimateUnitState) -> ClimateUnitState:
    return replace(state, target=Decimal(25), read_at=NOW + timedelta(seconds=POLL_INTERVAL))


def _changing_use_case(
    readings: tuple[ClimateUnitState | DeviceUnavailableError, ...],
    store: ClimateUnitStateStore,
    clock: MonotonicClockStub,
) -> PollClimateUnitsUseCase:
    DeviceFactory(slug='heater-office', kind=DeviceKind.CLIMATE_UNIT, driver=DeviceDriver.TUYA_HEATER)
    return PollClimateUnitsUseCase(
        devices=DeviceRepository(),
        provider=ClimateUnitProviderStub(units={'heater-office': ChangingClimateUnitStub(readings=readings)}),
        store=store,
        clock=clock,
        dispatcher=EventDispatcher(),
    )


@pytest.fixture
def f_changing_use_case(
    state: ClimateUnitState,
    f_changed: ClimateUnitState,
    f_store: ClimateUnitStateStore,
    clock: MonotonicClockStub,
) -> PollClimateUnitsUseCase:
    use_case = _changing_use_case((state, f_changed), f_store, clock)
    use_case.execute(PollClimateUnitsInputDTO(now=NOW))
    clock.advance(POLL_INTERVAL)
    return use_case


@pytest.fixture
def f_blinked_use_case(
    state: ClimateUnitState,
    f_changed: ClimateUnitState,
    f_store: ClimateUnitStateStore,
    clock: MonotonicClockStub,
) -> PollClimateUnitsUseCase:
    use_case = _changing_use_case((state, DeviceUnavailableError('timeout'), f_changed), f_store, clock)
    for _poll in range(2):
        use_case.execute(PollClimateUnitsInputDTO(now=NOW))
        clock.advance(POLL_INTERVAL)
    return use_case


def test_poll_reads_only_climate_units_and_records_states_and_failures(
    f_use_case: PollClimateUnitsUseCase,
    f_store: ClimateUnitStateStore,
    state: ClimateUnitState,
) -> None:
    output = f_use_case.execute(PollClimateUnitsInputDTO(now=NOW))

    failed = f_store.snapshot('heater-bedroom')
    assert (output, f_store.snapshot('heater-office').state, failed.error_code, failed.error) == (
        PollClimateUnitsOutputDTO(polled=('heater-office',), failed=('heater-bedroom',), pairs=()),
        state,
        'device_unavailable',
        'timeout',
    )


def test_poll_journals_each_availability_change_once(
    f_use_case: PollClimateUnitsUseCase,
    f_received: list[DomainEvent],
) -> None:
    f_use_case.execute(PollClimateUnitsInputDTO(now=NOW))
    f_use_case.execute(PollClimateUnitsInputDTO(now=NOW))

    assert f_received == [
        DeviceAvailabilityChanged(
            slug='heater-bedroom',
            available=False,
            reason='timeout',
            error_code='device_unavailable',
            occurred_at=NOW,
        ),
        DeviceAvailabilityChanged(
            slug='heater-office',
            available=True,
            reason='reading received',
            error_code=None,
            occurred_at=NOW,
        ),
    ]


def test_poll_pairs_two_good_readings_with_the_moments_they_were_stored(
    f_changing_use_case: PollClimateUnitsUseCase,
    state: ClimateUnitState,
    f_changed: ClimateUnitState,
) -> None:
    output = f_changing_use_case.execute(PollClimateUnitsInputDTO(now=NOW))

    assert output.pairs == (
        ReadingPair(
            slug='heater-office',
            before=state,
            after=f_changed,
            before_moment=0.0,
            after_moment=POLL_INTERVAL,
        ),
    )


def test_poll_pairs_nothing_across_a_failed_read(f_blinked_use_case: PollClimateUnitsUseCase) -> None:
    output = f_blinked_use_case.execute(PollClimateUnitsInputDTO(now=NOW))

    assert (output.polled, output.pairs) == (('heater-office',), ())
