from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.core.domain.events import DomainEvent, EventDispatcher
from apps.core.stubs import MonotonicClockStub
from apps.devices.domain.errors import DeviceUnavailableError
from apps.devices.domain.events import DeviceAvailabilityChanged
from apps.devices.domain.readings import ReadingPair, ThermostatMode, ThermostatState
from apps.devices.factories import DeviceFactory
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.state_store import ThermostatSnapshot, ThermostatStateStore
from apps.devices.stubs import ChangingThermostatStub, FloorThermostatStub, ThermostatProviderStub
from apps.devices.usecases.poll_thermostats import (
    PollThermostatsInputDTO,
    PollThermostatsOutputDTO,
    PollThermostatsUseCase,
)

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 29, 3, 0, tzinfo=UTC)
POLL_INTERVAL = 30.0


@pytest.fixture
def state() -> ThermostatState:
    return ThermostatState(
        power=True,
        mode=ThermostatMode.HOLD,
        setpoint=Decimal('22.0'),
        floor_temperature=Decimal('21.5'),
        heating=True,
        read_at=NOW,
    )


@pytest.fixture
def clock() -> MonotonicClockStub:
    return MonotonicClockStub(now=0.0)


@pytest.fixture
def f_store(clock: MonotonicClockStub) -> ThermostatStateStore:
    return ThermostatStateStore(unavailable_after=timedelta(minutes=10), clock=clock)


@pytest.fixture
def stale_store(state: ThermostatState, clock: MonotonicClockStub) -> ThermostatStateStore:
    store = ThermostatStateStore(unavailable_after=timedelta(minutes=10), clock=clock)
    store.record_state('thermostat-hallway', state)
    store.mark_reported('thermostat-hallway', True)
    clock.advance(11 * 60)
    store.record_state('thermostat-kitchen', state)
    store.mark_reported('thermostat-kitchen', True)
    return store


@pytest.fixture
def f_received() -> list[DomainEvent]:
    return []


@pytest.fixture
def provider(state: ThermostatState) -> ThermostatProviderStub:
    DeviceFactory(slug='thermostat-kitchen')
    DeviceFactory(slug='thermostat-hallway')
    return ThermostatProviderStub(
        thermostats={
            'thermostat-kitchen': FloorThermostatStub(state=state),
            'thermostat-hallway': FloorThermostatStub(state=None, error=DeviceUnavailableError('timeout')),
        },
    )


@pytest.fixture
def dispatcher(f_received: list[DomainEvent]) -> EventDispatcher:
    event_dispatcher = EventDispatcher()
    event_dispatcher.register(DeviceAvailabilityChanged, f_received.append)
    return event_dispatcher


@pytest.fixture
def f_use_case(
    provider: ThermostatProviderStub,
    f_store: ThermostatStateStore,
    clock: MonotonicClockStub,
    dispatcher: EventDispatcher,
) -> PollThermostatsUseCase:
    return PollThermostatsUseCase(
        devices=DeviceRepository(),
        provider=provider,
        store=f_store,
        clock=clock,
        dispatcher=dispatcher,
    )


@pytest.fixture
def f_stale_use_case(
    provider: ThermostatProviderStub,
    stale_store: ThermostatStateStore,
    clock: MonotonicClockStub,
    dispatcher: EventDispatcher,
) -> PollThermostatsUseCase:
    return PollThermostatsUseCase(
        devices=DeviceRepository(),
        provider=provider,
        store=stale_store,
        clock=clock,
        dispatcher=dispatcher,
    )


@pytest.fixture
def f_driverless_use_case(
    f_store: ThermostatStateStore,
    clock: MonotonicClockStub,
    dispatcher: EventDispatcher,
) -> PollThermostatsUseCase:
    DeviceFactory(slug='thermostat-attic')
    return PollThermostatsUseCase(
        devices=DeviceRepository(),
        provider=ThermostatProviderStub(thermostats={}),
        store=f_store,
        clock=clock,
        dispatcher=dispatcher,
    )


@pytest.fixture
def f_changed(state: ThermostatState) -> ThermostatState:
    return replace(state, setpoint=Decimal('25.0'), read_at=NOW + timedelta(seconds=POLL_INTERVAL))


@pytest.fixture
def f_polled_use_case(f_use_case: PollThermostatsUseCase, clock: MonotonicClockStub) -> PollThermostatsUseCase:
    f_use_case.execute(PollThermostatsInputDTO(now=NOW))
    clock.advance(POLL_INTERVAL)
    return f_use_case


def _changing_use_case(
    readings: tuple[ThermostatState | DeviceUnavailableError, ...],
    store: ThermostatStateStore,
    clock: MonotonicClockStub,
) -> PollThermostatsUseCase:
    DeviceFactory(slug='thermostat-kitchen')
    return PollThermostatsUseCase(
        devices=DeviceRepository(),
        provider=ThermostatProviderStub(thermostats={'thermostat-kitchen': ChangingThermostatStub(readings=readings)}),
        store=store,
        clock=clock,
        dispatcher=EventDispatcher(),
    )


@pytest.fixture
def f_changing_use_case(
    state: ThermostatState,
    f_changed: ThermostatState,
    f_store: ThermostatStateStore,
    clock: MonotonicClockStub,
) -> PollThermostatsUseCase:
    return _changing_use_case((state, f_changed), f_store, clock)


@pytest.fixture
def f_blinking_use_case(
    state: ThermostatState,
    f_changed: ThermostatState,
    f_store: ThermostatStateStore,
    clock: MonotonicClockStub,
) -> PollThermostatsUseCase:
    return _changing_use_case((state, DeviceUnavailableError('timeout'), f_changed), f_store, clock)


@pytest.fixture
def f_blinked_use_case(
    f_blinking_use_case: PollThermostatsUseCase,
    clock: MonotonicClockStub,
) -> PollThermostatsUseCase:
    for _poll in range(2):
        f_blinking_use_case.execute(PollThermostatsInputDTO(now=NOW))
        clock.advance(POLL_INTERVAL)
    return f_blinking_use_case


def test_poll_records_states_and_failures(f_use_case: PollThermostatsUseCase, f_store: ThermostatStateStore) -> None:
    output = f_use_case.execute(PollThermostatsInputDTO(now=NOW))

    snapshot = f_store.snapshot('thermostat-hallway')
    assert (output, snapshot.error_code, snapshot.error) == (
        PollThermostatsOutputDTO(polled=('thermostat-kitchen',), failed=('thermostat-hallway',), pairs=()),
        'device_unavailable',
        'timeout',
    )


def test_first_poll_reports_availability_once(
    f_use_case: PollThermostatsUseCase,
    f_received: list[DomainEvent],
) -> None:
    f_use_case.execute(PollThermostatsInputDTO(now=NOW))
    f_use_case.execute(PollThermostatsInputDTO(now=NOW))

    assert f_received == [
        DeviceAvailabilityChanged(
            slug='thermostat-hallway',
            available=False,
            reason='timeout',
            error_code='device_unavailable',
            occurred_at=NOW,
        ),
        DeviceAvailabilityChanged(
            slug='thermostat-kitchen',
            available=True,
            reason='reading received',
            error_code=None,
            occurred_at=NOW,
        ),
    ]


def test_stale_failing_device_is_reported_unavailable(
    f_stale_use_case: PollThermostatsUseCase,
    f_received: list[DomainEvent],
) -> None:
    f_stale_use_case.execute(PollThermostatsInputDTO(now=NOW))

    assert f_received == [
        DeviceAvailabilityChanged(
            slug='thermostat-hallway',
            available=False,
            reason='timeout',
            error_code='device_unavailable',
            occurred_at=NOW,
        ),
    ]


def test_poll_keeps_the_code_of_any_device_error(
    f_driverless_use_case: PollThermostatsUseCase,
    f_store: ThermostatStateStore,
    f_received: list[DomainEvent],
) -> None:
    f_driverless_use_case.execute(PollThermostatsInputDTO(now=NOW))

    assert (f_store.snapshot('thermostat-attic'), f_received) == (
        ThermostatSnapshot(
            slug='thermostat-attic',
            state=None,
            age=None,
            available=False,
            error_code='driver_not_implemented',
            error='driver not implemented: thermostat-attic',
        ),
        [
            DeviceAvailabilityChanged(
                slug='thermostat-attic',
                available=False,
                reason='driver not implemented: thermostat-attic',
                error_code='driver_not_implemented',
                occurred_at=NOW,
            ),
        ],
    )


def test_poll_pairs_two_good_reads_of_a_thermostat_with_the_moments_each_was_stored(
    f_changing_use_case: PollThermostatsUseCase,
    clock: MonotonicClockStub,
    state: ThermostatState,
    f_changed: ThermostatState,
) -> None:
    first = f_changing_use_case.execute(PollThermostatsInputDTO(now=NOW))
    clock.advance(POLL_INTERVAL)
    second = f_changing_use_case.execute(PollThermostatsInputDTO(now=NOW + timedelta(seconds=POLL_INTERVAL)))

    assert (first.pairs, second.pairs) == (
        (),
        (
            ReadingPair(
                slug='thermostat-kitchen',
                before=state,
                after=f_changed,
                before_moment=0.0,
                after_moment=POLL_INTERVAL,
            ),
        ),
    )


def test_poll_pairs_no_reads_with_a_failed_read_between_them(
    f_blinked_use_case: PollThermostatsUseCase,
) -> None:
    output = f_blinked_use_case.execute(PollThermostatsInputDTO(now=NOW))

    assert (output.polled, output.pairs) == (('thermostat-kitchen',), ())


def test_poll_pairs_nothing_for_a_thermostat_it_cannot_read(f_polled_use_case: PollThermostatsUseCase) -> None:
    output = f_polled_use_case.execute(PollThermostatsInputDTO(now=NOW))

    assert [pair.slug for pair in output.pairs] == ['thermostat-kitchen']
