from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from structlog.testing import capture_logs

from apps.core.stubs import MonotonicClockStub, WallClockStub
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.domain.readings import (
    ClimateMode,
    ClimateModeWord,
    ClimateUnitState,
    Phase,
    PhaseCurrents,
    TemperatureReading,
)
from apps.devices.drivers.esphome_messages import (
    EsphomeClimateSink,
    EsphomeMessageHandler,
    NodeDevice,
    PhaseMeterSink,
    PresenceSink,
    TemperatureSink,
)
from apps.devices.factories import DeviceFactory
from apps.devices.services.esphome_climate_store import EsphomeClimateSnapshot, EsphomeClimateStore
from apps.devices.services.phase_meter_store import PhaseMeterStore
from apps.devices.services.presence_store import PresenceSnapshot, PresenceStore
from apps.devices.services.state_store import TemperatureSnapshot, TemperatureStateStore

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
KITCHEN_TEMPERATURE = 'teplo/teplo-air-kitchen/sensor/temperature/state'
KITCHEN_HUMIDITY = 'teplo/teplo-air-kitchen/sensor/humidity/state'
KITCHEN_STATUS = 'teplo/teplo-air-kitchen/status'
OUTDOOR_STATUS = 'teplo/teplo-outdoor/status'
PC_PRESENCE = 'teplo/teplo-pc-office/sensor/presence/state'
OFFICE_TEMPERATURE = 'teplo/teplo-air-office/sensor/temperature/state'
OFFICE_PRESENCE = 'teplo/teplo-air-office/sensor/presence/state'
OFFICE_STATUS = 'teplo/teplo-air-office/status'
AC_MODE = 'teplo/teplo-ac-bedroom/climate/air_conditioner/mode/state'
AC_TARGET = 'teplo/teplo-ac-bedroom/climate/air_conditioner/target_temperature/state'
AC_TEMPERATURE = 'teplo/teplo-ac-bedroom/climate/air_conditioner/current_temperature/state'
AC_STATUS = 'teplo/teplo-ac-bedroom/status'
AC_HEATING = ClimateUnitState(
    mode=ClimateMode.HEAT,
    target=Decimal('20.5'),
    room_temperature=Decimal('19.5'),
    read_at=NOW,
)


@pytest.fixture
def monotonic() -> MonotonicClockStub:
    return MonotonicClockStub(now=1000.0)


@pytest.fixture
def wall_clock() -> WallClockStub:
    return WallClockStub(now=NOW)


@pytest.fixture
def f_temperature_store(monotonic: MonotonicClockStub) -> TemperatureStateStore:
    return TemperatureStateStore(unavailable_after=timedelta(minutes=10), clock=monotonic)


@pytest.fixture
def f_phase_store(monotonic: MonotonicClockStub) -> PhaseMeterStore:
    return PhaseMeterStore(stale_after=timedelta(seconds=10), unavailable_after=timedelta(minutes=10), clock=monotonic)


@pytest.fixture
def f_presence_store(monotonic: MonotonicClockStub) -> PresenceStore:
    return PresenceStore(unavailable_after=timedelta(minutes=10), clock=monotonic)


@pytest.fixture
def f_climate_store(monotonic: MonotonicClockStub) -> EsphomeClimateStore:
    return EsphomeClimateStore(clock=monotonic)


@pytest.fixture
def f_handler(
    f_temperature_store: TemperatureStateStore,
    f_phase_store: PhaseMeterStore,
    f_presence_store: PresenceStore,
    f_climate_store: EsphomeClimateStore,
    wall_clock: WallClockStub,
) -> EsphomeMessageHandler:
    temperature = TemperatureSink(store=f_temperature_store)
    presence = PresenceSink(store=f_presence_store)
    return EsphomeMessageHandler(
        nodes={
            'air-kitchen': (NodeDevice(slug='air-kitchen', sink=temperature),),
            'outdoor': (NodeDevice(slug='outdoor', sink=temperature),),
            'phase-meter': (NodeDevice(slug='phase-meter', sink=PhaseMeterSink(store=f_phase_store)),),
            'pc-office': (NodeDevice(slug='pc-office', sink=presence),),
            'air-office': (
                NodeDevice(slug='air-office', sink=temperature),
                NodeDevice(slug='presence-office', sink=presence),
            ),
            'ac-bedroom': (NodeDevice(slug='ac-bedroom', climate=EsphomeClimateSink(store=f_climate_store)),),
        },
        clock=wall_clock,
    )


@pytest.fixture
def f_read_handler(
    f_handler: EsphomeMessageHandler,
    monotonic: MonotonicClockStub,
    wall_clock: WallClockStub,
) -> EsphomeMessageHandler:
    f_handler.receive(KITCHEN_TEMPERATURE, b'21.5', retained=False)
    monotonic.advance(5)
    wall_clock.advance(timedelta(seconds=5))
    return f_handler


@pytest.fixture
def f_humid_handler(
    f_read_handler: EsphomeMessageHandler,
    monotonic: MonotonicClockStub,
    wall_clock: WallClockStub,
) -> EsphomeMessageHandler:
    f_read_handler.receive(KITCHEN_HUMIDITY, b'41.2', retained=False)
    monotonic.advance(25)
    wall_clock.advance(timedelta(seconds=25))
    return f_read_handler


@pytest.fixture
def f_disconnected_handler(f_read_handler: EsphomeMessageHandler) -> EsphomeMessageHandler:
    f_read_handler.receive(OUTDOOR_STATUS, b'offline', retained=False)
    f_read_handler.connection_lost('mqtt connection lost: Unspecified error')
    return f_read_handler


@pytest.fixture
def f_failing_handler(f_read_handler: EsphomeMessageHandler) -> EsphomeMessageHandler:
    f_read_handler.receive(KITCHEN_TEMPERATURE, b'nan', retained=False)
    f_read_handler.receive(OUTDOOR_STATUS, b'offline', retained=False)
    f_read_handler.receive('teplo/teplo-phase-meter/sensor/l2_current/state', b'4,10', retained=False)
    return f_read_handler


@pytest.fixture
def f_routed_handler() -> EsphomeMessageHandler:
    DeviceFactory(slug='air-kitchen', kind=DeviceKind.TEMPERATURE_SENSOR, driver=DeviceDriver.ESPHOME_TEMPERATURE)
    DeviceFactory(slug='phase-meter', kind=DeviceKind.PHASE_METER, driver=DeviceDriver.ESPHOME_PHASE_METER)
    DeviceFactory(slug='air-attic', kind=DeviceKind.TEMPERATURE_SENSOR, driver=DeviceDriver.UNSUPPORTED)
    DeviceFactory(slug='thermostat-kitchen')
    return EsphomeMessageHandler.init()


@pytest.fixture
def f_routed_presence_handler() -> EsphomeMessageHandler:
    DeviceFactory(slug='air-office', kind=DeviceKind.TEMPERATURE_SENSOR, driver=DeviceDriver.ESPHOME_TEMPERATURE)
    DeviceFactory(
        slug='presence-office',
        kind=DeviceKind.PRESENCE_SENSOR,
        driver=DeviceDriver.ESPHOME_PRESENCE,
        node='air-office',
    )
    DeviceFactory(slug='pc-office', kind=DeviceKind.PRESENCE_SENSOR, driver=DeviceDriver.ESPHOME_PRESENCE)
    return EsphomeMessageHandler.init()


@pytest.fixture
def f_routed_climate_handler() -> EsphomeMessageHandler:
    DeviceFactory(slug='ac-bedroom', kind=DeviceKind.CLIMATE_UNIT, driver=DeviceDriver.ESPHOME_CLIMATE)
    DeviceFactory(slug='heater-office', kind=DeviceKind.CLIMATE_UNIT, driver=DeviceDriver.TUYA_HEATER)
    return EsphomeMessageHandler.init()


@pytest.fixture
def f_climate_handler(f_handler: EsphomeMessageHandler) -> EsphomeMessageHandler:
    f_handler.receive(AC_MODE, b'heat', retained=False)
    f_handler.receive(AC_TEMPERATURE, b'19.5', retained=False)
    f_handler.receive(AC_TARGET, b'20.5', retained=False)
    return f_handler


@pytest.fixture
def present_handler(f_handler: EsphomeMessageHandler, monotonic: MonotonicClockStub) -> EsphomeMessageHandler:
    f_handler.receive(PC_PRESENCE, b'1', retained=False)
    monotonic.advance(30)
    return f_handler


@pytest.fixture
def f_presence_disconnected_handler(present_handler: EsphomeMessageHandler) -> EsphomeMessageHandler:
    present_handler.connection_lost('mqtt connection lost: Unspecified error')
    return present_handler


def test_temperature_becomes_a_reading_stamped_on_arrival(
    f_handler: EsphomeMessageHandler,
    f_temperature_store: TemperatureStateStore,
) -> None:
    f_handler.receive(KITCHEN_TEMPERATURE, b'21.5', retained=False)

    assert f_temperature_store.snapshot('air-kitchen') == TemperatureSnapshot(
        slug='air-kitchen',
        state=TemperatureReading(temperature=Decimal('21.5'), humidity=None, read_at=NOW),
        age=timedelta(0),
        available=True,
        error_code=None,
        error=None,
    )


def test_humidity_joins_the_latest_reading_without_making_it_younger(
    f_read_handler: EsphomeMessageHandler,
    f_temperature_store: TemperatureStateStore,
) -> None:
    f_read_handler.receive(KITCHEN_HUMIDITY, b'41.2', retained=False)

    snapshot = f_temperature_store.snapshot('air-kitchen')
    assert (snapshot.state, snapshot.age) == (
        TemperatureReading(temperature=Decimal('21.5'), humidity=Decimal('41.2'), read_at=NOW),
        timedelta(seconds=5),
    )


def test_next_temperature_keeps_the_humidity_until_a_new_one_arrives(
    f_humid_handler: EsphomeMessageHandler,
    f_temperature_store: TemperatureStateStore,
) -> None:
    f_humid_handler.receive(KITCHEN_TEMPERATURE, b'21.7', retained=False)

    snapshot = f_temperature_store.snapshot('air-kitchen')
    assert (snapshot.state, snapshot.age) == (
        TemperatureReading(
            temperature=Decimal('21.7'),
            humidity=Decimal('41.2'),
            read_at=NOW + timedelta(seconds=30),
        ),
        timedelta(0),
    )


def test_humidity_before_any_temperature_is_dropped(
    f_handler: EsphomeMessageHandler,
    f_temperature_store: TemperatureStateStore,
) -> None:
    f_handler.receive(KITCHEN_HUMIDITY, b'41.2', retained=False)

    assert f_temperature_store.snapshot('air-kitchen').state is None


def test_nan_temperature_records_a_failure_and_keeps_the_reading(
    f_read_handler: EsphomeMessageHandler,
    f_temperature_store: TemperatureStateStore,
) -> None:
    f_read_handler.receive(KITCHEN_TEMPERATURE, b'nan', retained=False)

    assert f_temperature_store.snapshot('air-kitchen') == TemperatureSnapshot(
        slug='air-kitchen',
        state=TemperatureReading(temperature=Decimal('21.5'), humidity=None, read_at=NOW),
        age=timedelta(seconds=5),
        available=True,
        error_code='sensor_reading_failed',
        error='temperature: the node reported nan',
    )


def test_failed_humidity_leaves_the_reading_without_humidity(
    f_humid_handler: EsphomeMessageHandler,
    f_temperature_store: TemperatureStateStore,
) -> None:
    f_humid_handler.receive(KITCHEN_HUMIDITY, b'nan', retained=False)

    snapshot = f_temperature_store.snapshot('air-kitchen')
    assert (snapshot.state, snapshot.error_code, snapshot.error) == (
        TemperatureReading(temperature=Decimal('21.5'), humidity=None, read_at=NOW),
        'sensor_reading_failed',
        'humidity: the node reported nan',
    )


def test_phase_currents_are_recorded_per_phase(
    f_handler: EsphomeMessageHandler,
    f_phase_store: PhaseMeterStore,
) -> None:
    f_handler.receive('teplo/teplo-phase-meter/sensor/l1_current/state', b'12.34', retained=False)
    f_handler.receive('teplo/teplo-phase-meter/sensor/l2_current/state', b'4.10', retained=False)
    f_handler.receive('teplo/teplo-phase-meter/sensor/l3_current/state', b'0.52', retained=False)

    snapshot = f_phase_store.snapshot('phase-meter')
    assert (snapshot.currents, snapshot.stale) == (
        PhaseCurrents(
            amps={Phase.A: Decimal('12.34'), Phase.B: Decimal('4.10'), Phase.C: Decimal('0.52')},
            read_at=NOW,
        ),
        False,
    )


def test_unreadable_current_is_a_protocol_error(
    f_handler: EsphomeMessageHandler,
    f_phase_store: PhaseMeterStore,
) -> None:
    f_handler.receive('teplo/teplo-phase-meter/sensor/l2_current/state', b'4,10', retained=False)

    snapshot = f_phase_store.snapshot('phase-meter')
    assert (snapshot.currents, snapshot.error_code, snapshot.error) == (
        None,
        'device_protocol_error',
        "l2_current: not a number: b'4,10'",
    )


@pytest.mark.parametrize(
    ('topic', 'payload', 'retained'),
    [
        (KITCHEN_TEMPERATURE, b'21.5', True),
        (KITCHEN_STATUS, b'offline', True),
        (KITCHEN_STATUS, b'online', False),
        ('teplo/teplo-air-kitchen/sensor/wifi_signal/state', b'-61', False),
        ('teplo/teplo-air-kitchen/sensor/l1_current/state', b'12.34', False),
        ('teplo/teplo-phase-meter/sensor/temperature/state', b'21.5', False),
        ('teplo/teplo-phase-meter/sensor/uptime/state', b'not a number', False),
        ('teplo/teplo-spare/sensor/temperature/state', b'21.5', False),
        ('teplo/air-kitchen/sensor/temperature/state', b'21.5', False),
    ],
    ids=[
        'retained-reading',
        'retained-status',
        'online',
        'diagnostic',
        'current-from-an-air-sensor',
        'temperature-from-the-phase-meter',
        'diagnostic-of-the-phase-meter',
        'unknown-node',
        'node-without-prefix',
    ],
)
def test_message_outside_the_devices_records_nothing(
    f_handler: EsphomeMessageHandler,
    f_temperature_store: TemperatureStateStore,
    f_phase_store: PhaseMeterStore,
    topic: str,
    payload: bytes,
    retained: bool,
) -> None:
    f_handler.receive(topic, payload, retained=retained)

    kitchen = f_temperature_store.snapshot('air-kitchen')
    phase_meter = f_phase_store.snapshot('phase-meter')
    assert (
        kitchen.state,
        kitchen.error,
        phase_meter.currents,
        phase_meter.error,
        f_temperature_store.snapshot('spare'),
    ) == (
        None,
        None,
        None,
        None,
        TemperatureSnapshot(slug='spare', state=None, age=None, available=False, error_code=None, error=None),
    )


def test_offline_node_records_the_failure(
    f_read_handler: EsphomeMessageHandler,
    f_temperature_store: TemperatureStateStore,
) -> None:
    f_read_handler.receive(KITCHEN_STATUS, b'offline', retained=False)

    assert f_temperature_store.snapshot('air-kitchen') == TemperatureSnapshot(
        slug='air-kitchen',
        state=TemperatureReading(temperature=Decimal('21.5'), humidity=None, read_at=NOW),
        age=timedelta(seconds=5),
        available=True,
        error_code='device_unavailable',
        error='the node went offline',
    )


def test_lost_broker_fails_every_device(
    f_read_handler: EsphomeMessageHandler,
    f_temperature_store: TemperatureStateStore,
    f_phase_store: PhaseMeterStore,
) -> None:
    f_read_handler.connection_lost('mqtt broker unreachable')

    failures = [
        (snapshot.error_code, snapshot.error)
        for snapshot in (
            f_temperature_store.snapshot('air-kitchen'),
            f_temperature_store.snapshot('outdoor'),
            f_phase_store.snapshot('phase-meter'),
        )
    ]
    assert failures == [('broker_unavailable', 'mqtt broker unreachable')] * 3


def test_connection_clears_the_lost_broker_and_keeps_the_reading(
    f_disconnected_handler: EsphomeMessageHandler,
    f_temperature_store: TemperatureStateStore,
    f_phase_store: PhaseMeterStore,
) -> None:
    f_disconnected_handler.connection_made()

    kitchen = f_temperature_store.snapshot('air-kitchen')
    failures = [
        (snapshot.error_code, snapshot.error)
        for snapshot in (kitchen, f_temperature_store.snapshot('outdoor'), f_phase_store.snapshot('phase-meter'))
    ]
    assert (kitchen.state, failures) == (
        TemperatureReading(temperature=Decimal('21.5'), humidity=None, read_at=NOW),
        [(None, None)] * 3,
    )


def test_connection_keeps_the_failures_the_nodes_reported(
    f_failing_handler: EsphomeMessageHandler,
    f_temperature_store: TemperatureStateStore,
    f_phase_store: PhaseMeterStore,
) -> None:
    f_failing_handler.connection_made()

    failures = [
        (snapshot.error_code, snapshot.error)
        for snapshot in (
            f_temperature_store.snapshot('air-kitchen'),
            f_temperature_store.snapshot('outdoor'),
            f_phase_store.snapshot('phase-meter'),
        )
    ]
    assert failures == [
        ('sensor_reading_failed', 'temperature: the node reported nan'),
        ('device_unavailable', 'the node went offline'),
        ('device_protocol_error', "l2_current: not a number: b'4,10'"),
    ]


def test_unknown_node_is_logged_once(f_handler: EsphomeMessageHandler) -> None:
    with capture_logs() as logs:
        f_handler.receive('teplo/teplo-spare/sensor/temperature/state', b'21.5', retained=False)
        f_handler.receive('teplo/teplo-spare/sensor/temperature/state', b'21.6', retained=False)

    assert logs == [{'event': 'esphome_node_unknown', 'node': 'teplo-spare', 'log_level': 'info'}]


def test_failing_reading_is_logged_again_only_after_it_recovered(f_handler: EsphomeMessageHandler) -> None:
    with capture_logs() as logs:
        for payload in (b'nan', b'nan', b'21.5', b'nan'):
            f_handler.receive(KITCHEN_TEMPERATURE, payload, retained=False)

    assert (
        logs
        == [
            {
                'event': 'esphome_reading_failed',
                'device': 'air-kitchen',
                'object_id': 'temperature',
                'error_code': 'sensor_reading_failed',
                'detail': 'temperature: the node reported nan',
                'log_level': 'warning',
            },
        ]
        * 2
    )


def test_init_routes_only_the_esphome_devices(f_routed_handler: EsphomeMessageHandler) -> None:
    with capture_logs() as logs:
        for node in ('teplo-air-kitchen', 'teplo-phase-meter', 'teplo-air-attic', 'teplo-thermostat-kitchen'):
            f_routed_handler.receive(f'teplo/{node}/status', b'online', retained=False)

    assert logs == [
        {'event': 'esphome_node_online', 'device': 'air-kitchen', 'log_level': 'info'},
        {'event': 'esphome_node_online', 'device': 'phase-meter', 'log_level': 'info'},
        {'event': 'esphome_node_unknown', 'node': 'teplo-air-attic', 'log_level': 'info'},
        {'event': 'esphome_node_unknown', 'node': 'teplo-thermostat-kitchen', 'log_level': 'info'},
    ]


def test_presence_report_reaches_the_device_on_its_node(
    f_handler: EsphomeMessageHandler,
    f_presence_store: PresenceStore,
) -> None:
    f_handler.receive(PC_PRESENCE, b'1', retained=False)

    assert f_presence_store.snapshot('pc-office') == PresenceSnapshot(
        slug='pc-office',
        present=True,
        read_at=NOW,
        age=timedelta(0),
        seen_age=timedelta(0),
        watched_for=timedelta(0),
        available=True,
        error_code=None,
        error=None,
    )


def test_node_of_two_devices_routes_each_reading_to_the_device_that_reads_it(
    f_handler: EsphomeMessageHandler,
    f_temperature_store: TemperatureStateStore,
    f_presence_store: PresenceStore,
) -> None:
    f_handler.receive(OFFICE_TEMPERATURE, b'20.5', retained=False)
    f_handler.receive(OFFICE_PRESENCE, b'0', retained=False)

    assert (
        f_temperature_store.snapshot('air-office').state,
        f_temperature_store.snapshot('presence-office').state,
        f_presence_store.snapshot('presence-office').present,
        f_presence_store.snapshot('air-office').present,
    ) == (TemperatureReading(temperature=Decimal('20.5'), humidity=None, read_at=NOW), None, False, None)


def test_unreadable_presence_fails_only_the_presence_device(
    f_handler: EsphomeMessageHandler,
    f_temperature_store: TemperatureStateStore,
    f_presence_store: PresenceStore,
) -> None:
    f_handler.receive(OFFICE_TEMPERATURE, b'20.5', retained=False)
    f_handler.receive(OFFICE_PRESENCE, b'2', retained=False)

    presence = f_presence_store.snapshot('presence-office')
    assert (
        f_temperature_store.snapshot('air-office').error_code,
        presence.available,
        presence.error_code,
        presence.error,
    ) == (None, False, 'device_protocol_error', "presence: not 0 or 1: b'2'")


def test_offline_node_fails_every_device_on_it(
    f_handler: EsphomeMessageHandler,
    f_temperature_store: TemperatureStateStore,
    f_presence_store: PresenceStore,
    f_phase_store: PhaseMeterStore,
) -> None:
    with capture_logs() as logs:
        f_handler.receive(OFFICE_STATUS, b'offline', retained=False)
        f_handler.receive('teplo/teplo-phase-meter/status', b'offline', retained=False)

    failures = [
        (snapshot.error_code, snapshot.error)
        for snapshot in (
            f_temperature_store.snapshot('air-office'),
            f_presence_store.snapshot('presence-office'),
            f_phase_store.snapshot('phase-meter'),
        )
    ]
    assert (failures, logs) == (
        [('device_unavailable', 'the node went offline')] * 3,
        [
            {'event': 'esphome_node_offline', 'device': 'air-office', 'log_level': 'warning'},
            {'event': 'esphome_node_offline', 'device': 'presence-office', 'log_level': 'warning'},
            {'event': 'esphome_node_offline', 'device': 'phase-meter', 'log_level': 'warning'},
        ],
    )


@pytest.mark.usefixtures('f_presence_disconnected_handler')
def test_lost_broker_makes_a_presence_device_unavailable_at_once(f_presence_store: PresenceStore) -> None:
    snapshot = f_presence_store.snapshot('pc-office')

    assert (snapshot.available, snapshot.error_code, snapshot.error) == (
        False,
        'broker_unavailable',
        'mqtt connection lost: Unspecified error',
    )


def test_connection_clears_the_lost_broker_but_a_presence_device_waits_for_its_next_report(
    f_presence_disconnected_handler: EsphomeMessageHandler,
    f_presence_store: PresenceStore,
) -> None:
    f_presence_disconnected_handler.connection_made()
    waiting = f_presence_store.snapshot('pc-office')
    f_presence_disconnected_handler.receive(PC_PRESENCE, b'0', retained=False)
    reported = f_presence_store.snapshot('pc-office')

    assert (
        (waiting.available, waiting.error_code),
        (reported.available, reported.present, reported.seen_age, reported.watched_for),
    ) == ((False, None), (True, False, timedelta(seconds=30), timedelta(0)))


def test_init_hears_a_device_on_the_node_it_names(f_routed_presence_handler: EsphomeMessageHandler) -> None:
    with capture_logs() as logs:
        for node in ('teplo-air-office', 'teplo-pc-office', 'teplo-presence-office'):
            f_routed_presence_handler.receive(f'teplo/{node}/status', b'online', retained=False)

    assert logs == [
        {'event': 'esphome_node_online', 'device': 'air-office', 'log_level': 'info'},
        {'event': 'esphome_node_online', 'device': 'presence-office', 'log_level': 'info'},
        {'event': 'esphome_node_online', 'device': 'pc-office', 'log_level': 'info'},
        {'event': 'esphome_node_unknown', 'node': 'teplo-presence-office', 'log_level': 'info'},
    ]


@pytest.mark.usefixtures('f_climate_handler')
def test_climate_states_reach_the_air_conditioner_on_its_node(f_climate_store: EsphomeClimateStore) -> None:
    assert f_climate_store.snapshot('ac-bedroom') == EsphomeClimateSnapshot(
        slug='ac-bedroom',
        state=AC_HEATING,
        age=timedelta(0),
        error_code=None,
        error=None,
    )


@pytest.mark.parametrize(
    ('topic', 'payload'),
    [
        ('teplo/teplo-ac-bedroom/sensor/outdoor_temperature/state', b'-3.0'),
        ('teplo/teplo-ac-bedroom/sensor/power_usage/state', b'nan'),
        ('teplo/teplo-ac-bedroom/sensor/wifi_signal/state', b'-71'),
        ('teplo/teplo-ac-bedroom/sensor/uptime/state', b'3600'),
        ('teplo/teplo-ac-bedroom/climate/air_conditioner/fan_mode/state', b'auto'),
        ('teplo/teplo-ac-bedroom/climate/display/mode/state', b'heat'),
        ('teplo/teplo-air-kitchen/climate/air_conditioner/mode/state', b'heat'),
    ],
    ids=[
        'outdoor_temperature',
        'power_usage',
        'wifi_signal',
        'uptime',
        'attribute_teplo_does_not_read',
        'another_climate',
        'climate_of_an_air_node',
    ],
)
def test_message_the_air_conditioner_does_not_read_records_nothing(
    f_climate_handler: EsphomeMessageHandler,
    f_climate_store: EsphomeClimateStore,
    f_temperature_store: TemperatureStateStore,
    topic: str,
    payload: bytes,
) -> None:
    with capture_logs() as logs:
        f_climate_handler.receive(topic, payload, retained=False)

    assert (f_climate_store.snapshot('ac-bedroom').state, f_temperature_store.snapshot('air-kitchen').state, logs) == (
        AC_HEATING,
        None,
        [],
    )


def test_mode_teplo_lumps_with_another_keeps_the_unit_s_own_word(
    f_climate_handler: EsphomeMessageHandler,
    f_climate_store: EsphomeClimateStore,
) -> None:
    f_climate_handler.receive(AC_MODE, b'fan_only', retained=False)

    assert f_climate_store.snapshot('ac-bedroom').state == replace(
        AC_HEATING,
        mode=ClimateMode.OTHER,
        mode_word=ClimateModeWord.FAN_ONLY,
    )


def test_room_temperature_the_unit_could_not_read_leaves_the_state_without_it(
    f_climate_handler: EsphomeMessageHandler,
    f_climate_store: EsphomeClimateStore,
) -> None:
    f_climate_handler.receive(AC_TEMPERATURE, b'nan', retained=False)

    snapshot = f_climate_store.snapshot('ac-bedroom')
    assert (snapshot.state, snapshot.error_code) == (replace(AC_HEATING, room_temperature=None), None)


def test_nan_target_records_a_failed_reading_logged_once(
    f_climate_handler: EsphomeMessageHandler,
    f_climate_store: EsphomeClimateStore,
) -> None:
    with capture_logs() as logs:
        f_climate_handler.receive(AC_TARGET, b'nan', retained=False)
        f_climate_handler.receive(AC_TARGET, b'nan', retained=False)

    snapshot = f_climate_store.snapshot('ac-bedroom')
    assert (snapshot.state, snapshot.error_code, snapshot.error, logs) == (
        AC_HEATING,
        'sensor_reading_failed',
        'target_temperature: the node reported nan',
        [
            {
                'event': 'esphome_reading_failed',
                'device': 'ac-bedroom',
                'object_id': 'target_temperature',
                'error_code': 'sensor_reading_failed',
                'detail': 'target_temperature: the node reported nan',
                'log_level': 'warning',
            },
        ],
    )


def test_mode_that_is_not_a_word_records_a_protocol_error(
    f_climate_handler: EsphomeMessageHandler,
    f_climate_store: EsphomeClimateStore,
) -> None:
    f_climate_handler.receive(AC_MODE, b'\xff', retained=False)

    snapshot = f_climate_store.snapshot('ac-bedroom')
    assert (snapshot.error_code, snapshot.error) == ('device_protocol_error', "mode: not a mode: b'\\xff'")


def test_offline_air_conditioner_node_fails_the_unit_and_keeps_its_state(
    f_climate_handler: EsphomeMessageHandler,
    f_climate_store: EsphomeClimateStore,
) -> None:
    with capture_logs() as logs:
        f_climate_handler.receive(AC_STATUS, b'offline', retained=False)

    snapshot = f_climate_store.snapshot('ac-bedroom')
    assert (snapshot.state, snapshot.error_code, snapshot.error, logs) == (
        AC_HEATING,
        'device_unavailable',
        'the node went offline',
        [{'event': 'esphome_node_offline', 'device': 'ac-bedroom', 'log_level': 'warning'}],
    )


def test_lost_broker_fails_the_air_conditioner_until_the_connection_returns(
    f_climate_handler: EsphomeMessageHandler,
    f_climate_store: EsphomeClimateStore,
) -> None:
    f_climate_handler.connection_lost('mqtt broker unreachable')
    lost = f_climate_store.snapshot('ac-bedroom')
    f_climate_handler.connection_made()
    back = f_climate_store.snapshot('ac-bedroom')

    assert ((lost.error_code, lost.error), (back.state, back.error_code)) == (
        ('broker_unavailable', 'mqtt broker unreachable'),
        (AC_HEATING, None),
    )


def test_init_hears_an_air_conditioner_on_its_node(f_routed_climate_handler: EsphomeMessageHandler) -> None:
    with capture_logs() as logs:
        for node in ('teplo-ac-bedroom', 'teplo-heater-office'):
            f_routed_climate_handler.receive(f'teplo/{node}/status', b'online', retained=False)

    assert logs == [
        {'event': 'esphome_node_online', 'device': 'ac-bedroom', 'log_level': 'info'},
        {'event': 'esphome_node_unknown', 'node': 'teplo-heater-office', 'log_level': 'info'},
    ]
