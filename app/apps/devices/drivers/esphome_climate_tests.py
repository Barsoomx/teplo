from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from apps.core.domain.errors import DomainError
from apps.core.stubs import MonotonicClockStub
from apps.devices.domain.errors import BrokerUnavailableError
from apps.devices.domain.readings import ClimateMode, ClimateUnitState
from apps.devices.drivers.esphome_climate import EsphomeClimateDriver
from apps.devices.drivers.esphome_messages import EsphomeClimateSink, EsphomeMessageHandler, NodeDevice
from apps.devices.drivers.esphome_protocol import MODE, TARGET_TEMPERATURE
from apps.devices.services.esphome_climate_store import EsphomeClimateStore
from apps.devices.stubs import CommandPublisherStub

NOW = datetime(2026, 11, 5, 21, 0, tzinfo=UTC)
STATUS = 'teplo/teplo-ac-bedroom/status'
MODE_COMMAND = 'teplo/teplo-ac-bedroom/climate/air_conditioner/mode/command'
TARGET_COMMAND = 'teplo/teplo-ac-bedroom/climate/air_conditioner/target_temperature/command'
OFF_AT_20 = ClimateUnitState(
    mode=ClimateMode.OFF,
    target=Decimal('20.0'),
    room_temperature=Decimal('18.5'),
    read_at=NOW,
)


def _node(store: EsphomeClimateStore) -> EsphomeMessageHandler:
    return EsphomeMessageHandler(
        nodes={'ac-bedroom': (NodeDevice(slug='ac-bedroom', climate=EsphomeClimateSink(store=store)),)},
        clock=lambda: NOW,
    )


def _publish_state(node: EsphomeMessageHandler, state: Mapping[str, bytes]) -> None:
    for attribute, payload in state.items():
        node.receive(f'teplo/teplo-ac-bedroom/climate/air_conditioner/{attribute}/state', payload, retained=False)


def _node_echo(store: EsphomeClimateStore) -> Callable[[str, bytes], None]:
    node = _node(store)
    state = {MODE: b'off', TARGET_TEMPERATURE: b'20.0'}

    def echo(topic: str, payload: bytes) -> None:
        state[topic.split('/')[-2]] = payload
        _publish_state(node, state)

    return echo


def _node_echoing_the_mode_alone(store: EsphomeClimateStore) -> Callable[[str, bytes], None]:
    node = _node(store)

    def echo(topic: str, payload: bytes) -> None:
        _publish_state(node, {topic.split('/')[-2]: payload})

    return echo


def _node_booting(store: EsphomeClimateStore) -> Callable[[str, bytes], None]:
    node = _node(store)

    def boot(topic: str, payload: bytes) -> None:  # noqa: ARG001
        _publish_state(node, {MODE: b'off', TARGET_TEMPERATURE: b'nan'})

    return boot


def _node_rebooting(store: EsphomeClimateStore) -> Callable[[str, bytes], None]:
    node = _node(store)

    def reboot(topic: str, payload: bytes) -> None:  # noqa: ARG001
        node.receive(STATUS, b'offline', retained=False)
        _publish_state(node, {MODE: b'off', TARGET_TEMPERATURE: b'nan'})
        _publish_state(node, {TARGET_TEMPERATURE: b'20.0'})

    return reboot


@pytest.fixture
def clock() -> MonotonicClockStub:
    return MonotonicClockStub(now=1000.0)


@pytest.fixture
def store(clock: MonotonicClockStub) -> EsphomeClimateStore:
    store = EsphomeClimateStore(clock=clock)
    store.record_mode('ac-bedroom', ClimateMode.OFF, NOW)
    store.record_target('ac-bedroom', Decimal('20.0'), NOW)
    store.record_room_temperature('ac-bedroom', Decimal('18.5'))
    return store


@pytest.fixture
def f_echoing_publisher(store: EsphomeClimateStore) -> CommandPublisherStub:
    return CommandPublisherStub(echo=_node_echo(store))


@pytest.fixture
def f_silent_publisher() -> CommandPublisherStub:
    return CommandPublisherStub()


@pytest.fixture
def booting_publisher(store: EsphomeClimateStore) -> CommandPublisherStub:
    return CommandPublisherStub(echo=_node_booting(store))


@pytest.fixture
def rebooting_publisher(store: EsphomeClimateStore) -> CommandPublisherStub:
    return CommandPublisherStub(echo=_node_rebooting(store))


@pytest.fixture
def f_driver(store: EsphomeClimateStore, f_echoing_publisher: CommandPublisherStub) -> EsphomeClimateDriver:
    return EsphomeClimateDriver(
        slug='ac-bedroom',
        node='ac-bedroom',
        store=store,
        publisher=f_echoing_publisher,
        echo_timeout=0,
    )


@pytest.fixture
def f_silent_driver(store: EsphomeClimateStore, f_silent_publisher: CommandPublisherStub) -> EsphomeClimateDriver:
    return EsphomeClimateDriver(
        slug='ac-bedroom',
        node='ac-bedroom',
        store=store,
        publisher=f_silent_publisher,
        echo_timeout=0,
    )


@pytest.fixture
def f_driver_without_a_broker(store: EsphomeClimateStore) -> EsphomeClimateDriver:
    return EsphomeClimateDriver(
        slug='ac-bedroom',
        node='ac-bedroom',
        store=store,
        publisher=CommandPublisherStub(error=BrokerUnavailableError('mqtt broker not connected')),
        echo_timeout=0,
    )


@pytest.fixture
def f_driver_heard_90_seconds_ago(f_driver: EsphomeClimateDriver, clock: MonotonicClockStub) -> EsphomeClimateDriver:
    clock.advance(90)
    return f_driver


@pytest.fixture
def f_stale_driver(f_driver: EsphomeClimateDriver, clock: MonotonicClockStub) -> EsphomeClimateDriver:
    clock.advance(91)
    return f_driver


@pytest.fixture
def f_heating_driver(f_driver: EsphomeClimateDriver, store: EsphomeClimateStore) -> EsphomeClimateDriver:
    store.record_mode('ac-bedroom', ClimateMode.HEAT, NOW)
    return f_driver


@pytest.fixture
def f_driver_of_a_heating_unit_whose_node_reboots(
    store: EsphomeClimateStore,
    booting_publisher: CommandPublisherStub,
) -> EsphomeClimateDriver:
    store.record_mode('ac-bedroom', ClimateMode.HEAT, NOW)
    return EsphomeClimateDriver(
        slug='ac-bedroom',
        node='ac-bedroom',
        store=store,
        publisher=booting_publisher,
        echo_timeout=0,
    )


@pytest.fixture
def f_driver_of_a_heating_unit_whose_node_goes_offline_and_reboots(
    store: EsphomeClimateStore,
    rebooting_publisher: CommandPublisherStub,
) -> EsphomeClimateDriver:
    store.record_mode('ac-bedroom', ClimateMode.HEAT, NOW)
    return EsphomeClimateDriver(
        slug='ac-bedroom',
        node='ac-bedroom',
        store=store,
        publisher=rebooting_publisher,
        echo_timeout=0,
    )


@pytest.fixture
def f_driver_echoed_by_the_mode_alone(store: EsphomeClimateStore) -> EsphomeClimateDriver:
    return EsphomeClimateDriver(
        slug='ac-bedroom',
        node='ac-bedroom',
        store=store,
        publisher=CommandPublisherStub(echo=_node_echoing_the_mode_alone(store)),
        echo_timeout=0,
    )


@pytest.fixture
def f_driver_of_a_node_back_from_an_outage(
    f_driver: EsphomeClimateDriver,
    store: EsphomeClimateStore,
) -> EsphomeClimateDriver:
    node = _node(store)
    node.receive(STATUS, b'offline', retained=False)
    _publish_state(node, {MODE: b'heat', TARGET_TEMPERATURE: b'21.5'})
    return f_driver


@pytest.fixture
def f_failed_driver(f_driver: EsphomeClimateDriver, store: EsphomeClimateStore) -> EsphomeClimateDriver:
    store.record_failure('ac-bedroom', error_code='sensor_reading_failed', error='target_temperature: nan')
    return f_driver


@pytest.fixture
def f_unheard_driver(store: EsphomeClimateStore, f_silent_publisher: CommandPublisherStub) -> EsphomeClimateDriver:
    return EsphomeClimateDriver(
        slug='ac-office',
        node='ac-office',
        store=store,
        publisher=f_silent_publisher,
        echo_timeout=0,
    )


def test_read_gives_the_state_the_node_reported_within_90_seconds(
    f_driver_heard_90_seconds_ago: EsphomeClimateDriver,
) -> None:
    assert f_driver_heard_90_seconds_ago.read_state() == OFF_AT_20


@pytest.mark.parametrize(
    ('driver', 'failure'),
    [
        ('f_stale_driver', ('device_unavailable', 'no recent state from the node')),
        ('f_unheard_driver', ('device_unavailable', 'no recent state from the node')),
        ('f_failed_driver', ('sensor_reading_failed', 'target_temperature: nan')),
    ],
    ids=['older_than_90_seconds', 'never_heard', 'failure_recorded'],
)
def test_read_of_a_unit_without_a_fresh_state_raises_with_its_code(
    driver: str,
    request: pytest.FixtureRequest,
    failure: tuple[str, str],
) -> None:
    unread: EsphomeClimateDriver = request.getfixturevalue(driver)

    with pytest.raises(DomainError) as raised:
        unread.read_state()

    assert (raised.value.error_code, raised.value.detail) == failure


def test_switch_on_sends_heat_and_succeeds_on_the_echo(
    f_driver: EsphomeClimateDriver,
    f_echoing_publisher: CommandPublisherStub,
) -> None:
    f_driver.set_power(True)

    assert (f_echoing_publisher.published, f_driver.read_state().mode) == (
        [(MODE_COMMAND, b'heat')],
        ClimateMode.HEAT,
    )


def test_switch_off_sends_off_and_succeeds_on_the_echo(
    f_heating_driver: EsphomeClimateDriver,
    f_echoing_publisher: CommandPublisherStub,
) -> None:
    f_heating_driver.set_power(False)

    assert (f_echoing_publisher.published, f_heating_driver.read_state().mode) == (
        [(MODE_COMMAND, b'off')],
        ClimateMode.OFF,
    )


def test_switch_off_without_an_echo_fails_though_the_unit_last_reported_off(
    f_silent_driver: EsphomeClimateDriver,
    f_silent_publisher: CommandPublisherStub,
) -> None:
    with pytest.raises(DomainError) as raised:
        f_silent_driver.set_power(False)

    assert (f_silent_publisher.published, raised.value.error_code, raised.value.detail) == (
        [(MODE_COMMAND, b'off')],
        'device_protocol_error',
        'the unit did not switch off',
    )


def test_switch_off_fails_on_the_off_and_nan_a_node_reports_as_it_boots(
    f_driver_of_a_heating_unit_whose_node_reboots: EsphomeClimateDriver,
) -> None:
    with pytest.raises(DomainError) as raised:
        f_driver_of_a_heating_unit_whose_node_reboots.set_power(False)

    assert (raised.value.error_code, raised.value.detail) == ('device_protocol_error', 'the unit did not switch off')


def test_node_that_goes_offline_and_boots_gives_no_echo_and_no_state_on_its_off_nan_and_a_target_alone(
    f_driver_of_a_heating_unit_whose_node_goes_offline_and_reboots: EsphomeClimateDriver,
) -> None:
    with pytest.raises(DomainError) as switched:
        f_driver_of_a_heating_unit_whose_node_goes_offline_and_reboots.set_power(False)
    with pytest.raises(DomainError) as read:
        f_driver_of_a_heating_unit_whose_node_goes_offline_and_reboots.read_state()

    assert (
        (switched.value.error_code, switched.value.detail),
        (read.value.error_code, read.value.detail),
    ) == (
        ('device_protocol_error', 'the unit did not switch off'),
        ('sensor_reading_failed', 'target_temperature: the node reported nan'),
    )


def test_read_gives_the_state_a_node_reports_whole_after_an_outage(
    f_driver_of_a_node_back_from_an_outage: EsphomeClimateDriver,
) -> None:
    assert f_driver_of_a_node_back_from_an_outage.read_state() == replace(
        OFF_AT_20,
        mode=ClimateMode.HEAT,
        target=Decimal('21.5'),
    )


def test_switch_on_fails_when_only_the_mode_arrives_after_it(
    f_driver_echoed_by_the_mode_alone: EsphomeClimateDriver,
) -> None:
    with pytest.raises(DomainError) as raised:
        f_driver_echoed_by_the_mode_alone.set_power(True)

    assert (raised.value.error_code, raised.value.detail) == ('device_protocol_error', 'the unit did not switch on')


def test_switch_on_without_an_echo_fails(
    f_silent_driver: EsphomeClimateDriver,
    f_silent_publisher: CommandPublisherStub,
) -> None:
    with pytest.raises(DomainError) as raised:
        f_silent_driver.set_power(True)

    assert (f_silent_publisher.published, raised.value.error_code, raised.value.detail) == (
        [(MODE_COMMAND, b'heat')],
        'device_protocol_error',
        'the unit did not switch on',
    )


def test_setpoint_goes_out_with_one_decimal_and_succeeds_on_the_echo(
    f_driver: EsphomeClimateDriver,
    f_echoing_publisher: CommandPublisherStub,
) -> None:
    f_driver.set_setpoint(Decimal('21.5'))

    assert (f_echoing_publisher.published, f_driver.read_state().target) == (
        [(TARGET_COMMAND, b'21.5')],
        Decimal('21.5'),
    )


def test_setpoint_without_an_echo_fails(
    f_silent_driver: EsphomeClimateDriver,
    f_silent_publisher: CommandPublisherStub,
) -> None:
    with pytest.raises(DomainError) as raised:
        f_silent_driver.set_setpoint(Decimal(22))

    assert (f_silent_publisher.published, raised.value.error_code, raised.value.detail) == (
        [(TARGET_COMMAND, b'22.0')],
        'device_protocol_error',
        'the unit did not take the setpoint 22',
    )


def test_setpoint_off_the_half_degree_step_is_refused_before_anything_goes_out(
    f_driver: EsphomeClimateDriver,
    f_echoing_publisher: CommandPublisherStub,
) -> None:
    with pytest.raises(DomainError) as raised:
        f_driver.set_setpoint(Decimal('21.3'))

    assert (f_echoing_publisher.published, raised.value.error_code, raised.value.detail) == (
        [],
        'device_protocol_error',
        'the unit takes half degrees, not 21.3',
    )


@pytest.mark.parametrize(
    'command',
    [lambda driver: driver.set_power(True), lambda driver: driver.set_setpoint(Decimal('21.5'))],
    ids=['power', 'setpoint'],
)
def test_command_without_a_broker_fails_as_a_lost_broker(
    f_driver_without_a_broker: EsphomeClimateDriver,
    command: Callable[[EsphomeClimateDriver], None],
) -> None:
    with pytest.raises(DomainError) as raised:
        command(f_driver_without_a_broker)

    assert (raised.value.error_code, raised.value.detail) == ('broker_unavailable', 'mqtt broker not connected')
