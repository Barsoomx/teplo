from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.core.domain.events import DomainEvent, EventDispatcher
from apps.devices.domain.errors import DeviceUnavailableError
from apps.devices.domain.events import DeviceAvailabilityChanged
from apps.devices.domain.readings import ThermostatMode, ThermostatState
from apps.devices.factories import DeviceFactory
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.state_store import ThermostatSnapshot, ThermostatStateStore
from apps.devices.stubs import FloorThermostatStub, ThermostatProviderStub
from apps.devices.usecases.poll_thermostats import (
    PollThermostatsInputDTO,
    PollThermostatsOutputDTO,
    PollThermostatsUseCase,
)

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 29, 3, 0, tzinfo=UTC)


@pytest.fixture
def state() -> ThermostatState:
    return ThermostatState(
        power=True,
        mode=ThermostatMode.HOLD,
        setpoint=Decimal('22.0'),
        floor_temperature=Decimal('21.5'),
        read_at=NOW,
    )


@pytest.fixture
def f_store() -> ThermostatStateStore:
    return ThermostatStateStore(unavailable_after=timedelta(minutes=10))


@pytest.fixture
def stale_store(state: ThermostatState) -> ThermostatStateStore:
    store = ThermostatStateStore(unavailable_after=timedelta(minutes=10))
    store.record_state('thermostat-hallway', replace(state, read_at=NOW - timedelta(minutes=11)))
    store.mark_reported('thermostat-hallway', True)
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
    dispatcher: EventDispatcher,
) -> PollThermostatsUseCase:
    return PollThermostatsUseCase(devices=DeviceRepository(), provider=provider, store=f_store, dispatcher=dispatcher)


@pytest.fixture
def f_stale_use_case(
    provider: ThermostatProviderStub,
    stale_store: ThermostatStateStore,
    dispatcher: EventDispatcher,
) -> PollThermostatsUseCase:
    return PollThermostatsUseCase(
        devices=DeviceRepository(),
        provider=provider,
        store=stale_store,
        dispatcher=dispatcher,
    )


@pytest.fixture
def f_driverless_use_case(f_store: ThermostatStateStore, dispatcher: EventDispatcher) -> PollThermostatsUseCase:
    DeviceFactory(slug='thermostat-attic')
    return PollThermostatsUseCase(
        devices=DeviceRepository(),
        provider=ThermostatProviderStub(thermostats={}),
        store=f_store,
        dispatcher=dispatcher,
    )


def test_poll_records_states_and_failures(f_use_case: PollThermostatsUseCase, f_store: ThermostatStateStore) -> None:
    output = f_use_case.execute(PollThermostatsInputDTO(now=NOW))

    snapshot = f_store.snapshot('thermostat-hallway', NOW)
    assert (output, snapshot.error_code, snapshot.error) == (
        PollThermostatsOutputDTO(polled=('thermostat-kitchen',), failed=('thermostat-hallway',)),
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

    assert (f_store.snapshot('thermostat-attic', NOW), f_received) == (
        ThermostatSnapshot(
            slug='thermostat-attic',
            state=None,
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
