from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.core.domain.events import DomainEvent, EventDispatcher
from apps.core.services.secrets import DeviceSecret, SecretsFile
from apps.core.stubs import MonotonicClockStub
from apps.devices.domain.capabilities import MeteredPlug
from apps.devices.domain.errors import DeviceUnavailableError
from apps.devices.domain.events import DeviceAvailabilityChanged
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.domain.readings import PlugState
from apps.devices.drivers.tuya_client import tuya_client
from apps.devices.factories import DeviceFactory
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.driver_registry import DriverRegistry, PlugFactory
from apps.devices.services.state_store import PlugSnapshot, PlugStateStore
from apps.devices.stubs import MeteredPlugStub, PlugProviderStub
from apps.devices.usecases.poll_plugs import PollPlugsInputDTO, PollPlugsOutputDTO, PollPlugsUseCase

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
LOCAL_KEY = '0123456789abcdef'


def _checked_plug_factory(state: PlugState) -> PlugFactory:
    def build(secret: DeviceSecret) -> MeteredPlug:
        tuya_client(secret)
        return MeteredPlugStub(state=state)

    return build


@pytest.fixture
def f_state() -> PlugState:
    return PlugState(
        on=True,
        power_watts=Decimal('1305.4'),
        current_amps=Decimal('6.02'),
        voltage_volts=Decimal('216.8'),
        read_at=NOW,
    )


@pytest.fixture
def clock() -> MonotonicClockStub:
    return MonotonicClockStub(now=0.0)


@pytest.fixture
def f_store(clock: MonotonicClockStub) -> PlugStateStore:
    return PlugStateStore(unavailable_after=timedelta(minutes=10), clock=clock)


@pytest.fixture
def stale_store(f_state: PlugState, clock: MonotonicClockStub) -> PlugStateStore:
    store = PlugStateStore(unavailable_after=timedelta(minutes=10), clock=clock)
    store.record_state('plug-boiler-2f', f_state)
    store.mark_reported('plug-boiler-2f', True)
    clock.advance(11 * 60)
    store.record_state('plug-boiler-1f', f_state)
    store.mark_reported('plug-boiler-1f', True)
    return store


@pytest.fixture
def f_received() -> list[DomainEvent]:
    return []


@pytest.fixture
def provider(f_state: PlugState) -> PlugProviderStub:
    DeviceFactory(slug='thermostat-kitchen')
    DeviceFactory(slug='plug-boiler-1f', kind=DeviceKind.PLUG, driver=DeviceDriver.TUYA_PLUG)
    DeviceFactory(slug='plug-boiler-2f', kind=DeviceKind.PLUG, driver=DeviceDriver.TUYA_PLUG)
    return PlugProviderStub(
        plugs={
            'plug-boiler-1f': MeteredPlugStub(state=f_state),
            'plug-boiler-2f': MeteredPlugStub(state=None, error=DeviceUnavailableError('timeout')),
        },
    )


@pytest.fixture
def dispatcher(f_received: list[DomainEvent]) -> EventDispatcher:
    event_dispatcher = EventDispatcher()
    event_dispatcher.register(DeviceAvailabilityChanged, f_received.append)
    return event_dispatcher


@pytest.fixture
def f_use_case(provider: PlugProviderStub, f_store: PlugStateStore, dispatcher: EventDispatcher) -> PollPlugsUseCase:
    return PollPlugsUseCase(devices=DeviceRepository(), provider=provider, store=f_store, dispatcher=dispatcher)


@pytest.fixture
def f_stale_use_case(
    provider: PlugProviderStub,
    stale_store: PlugStateStore,
    dispatcher: EventDispatcher,
) -> PollPlugsUseCase:
    return PollPlugsUseCase(devices=DeviceRepository(), provider=provider, store=stale_store, dispatcher=dispatcher)


@pytest.fixture
def f_misconfigured_use_case(
    f_state: PlugState,
    f_store: PlugStateStore,
    dispatcher: EventDispatcher,
) -> PollPlugsUseCase:
    DeviceFactory(slug='plug-boiler-1f', kind=DeviceKind.PLUG, driver=DeviceDriver.TUYA_PLUG)
    DeviceFactory(slug='plug-boiler-2f', kind=DeviceKind.PLUG, driver=DeviceDriver.TUYA_PLUG)
    secrets = SecretsFile(
        devices={
            'plug-boiler-1f': DeviceSecret(
                address='192.0.2.11',
                device_id='plug-1f',
                local_key=LOCAL_KEY,
                protocol='3,5',
            ),
            'plug-boiler-2f': DeviceSecret(
                address='192.0.2.12',
                device_id='plug-2f',
                local_key=LOCAL_KEY,
                protocol='3.5',
            ),
        },
        panel_password=None,
    )
    registry = DriverRegistry(
        devices=DeviceRepository(),
        secrets=secrets,
        thermostat_factories={},
        plug_factories={DeviceDriver.TUYA_PLUG: _checked_plug_factory(f_state)},
    )
    return PollPlugsUseCase(devices=DeviceRepository(), provider=registry, store=f_store, dispatcher=dispatcher)


@pytest.fixture
def f_driverless_use_case(f_store: PlugStateStore, dispatcher: EventDispatcher) -> PollPlugsUseCase:
    DeviceFactory(slug='plug-washer', kind=DeviceKind.PLUG, driver=DeviceDriver.TUYA_PLUG)
    return PollPlugsUseCase(
        devices=DeviceRepository(),
        provider=PlugProviderStub(plugs={}),
        store=f_store,
        dispatcher=dispatcher,
    )


def test_poll_reads_only_plugs_and_records_states_and_failures(
    f_use_case: PollPlugsUseCase,
    f_store: PlugStateStore,
    f_state: PlugState,
) -> None:
    output = f_use_case.execute(PollPlugsInputDTO(now=NOW))

    assert (output, f_store.snapshot('plug-boiler-1f'), f_store.snapshot('plug-boiler-2f')) == (
        PollPlugsOutputDTO(polled=('plug-boiler-1f',), failed=('plug-boiler-2f',)),
        PlugSnapshot(
            slug='plug-boiler-1f',
            state=f_state,
            age=timedelta(0),
            available=True,
            error_code=None,
            error=None,
        ),
        PlugSnapshot(
            slug='plug-boiler-2f',
            state=None,
            age=None,
            available=False,
            error_code='device_unavailable',
            error='timeout',
        ),
    )


def test_first_poll_reports_availability_once(f_use_case: PollPlugsUseCase, f_received: list[DomainEvent]) -> None:
    f_use_case.execute(PollPlugsInputDTO(now=NOW))
    f_use_case.execute(PollPlugsInputDTO(now=NOW))

    assert f_received == [
        DeviceAvailabilityChanged(
            slug='plug-boiler-1f',
            available=True,
            reason='reading received',
            error_code=None,
            occurred_at=NOW,
        ),
        DeviceAvailabilityChanged(
            slug='plug-boiler-2f',
            available=False,
            reason='timeout',
            error_code='device_unavailable',
            occurred_at=NOW,
        ),
    ]


def test_stale_failing_plug_is_reported_unavailable(
    f_stale_use_case: PollPlugsUseCase,
    f_received: list[DomainEvent],
) -> None:
    f_stale_use_case.execute(PollPlugsInputDTO(now=NOW))

    assert f_received == [
        DeviceAvailabilityChanged(
            slug='plug-boiler-2f',
            available=False,
            reason='timeout',
            error_code='device_unavailable',
            occurred_at=NOW,
        ),
    ]


def test_poll_keeps_the_code_of_any_device_error(
    f_driverless_use_case: PollPlugsUseCase,
    f_store: PlugStateStore,
    f_received: list[DomainEvent],
) -> None:
    f_driverless_use_case.execute(PollPlugsInputDTO(now=NOW))

    assert (f_store.snapshot('plug-washer'), f_received) == (
        PlugSnapshot(
            slug='plug-washer',
            state=None,
            age=None,
            available=False,
            error_code='driver_not_implemented',
            error='driver not implemented: plug-washer',
        ),
        [
            DeviceAvailabilityChanged(
                slug='plug-washer',
                available=False,
                reason='driver not implemented: plug-washer',
                error_code='driver_not_implemented',
                occurred_at=NOW,
            ),
        ],
    )


def test_plug_with_a_malformed_secret_fails_alone(
    f_misconfigured_use_case: PollPlugsUseCase,
    f_store: PlugStateStore,
    f_state: PlugState,
) -> None:
    output = f_misconfigured_use_case.execute(PollPlugsInputDTO(now=NOW))

    misconfigured = f_store.snapshot('plug-boiler-1f')
    assert (output, misconfigured.error_code, misconfigured.error, f_store.snapshot('plug-boiler-2f').state) == (
        PollPlugsOutputDTO(polled=('plug-boiler-2f',), failed=('plug-boiler-1f',)),
        'device_config_invalid',
        "protocol '3,5' is not one of 3.1, 3.2, 3.3, 3.4, 3.5",
        f_state,
    )
