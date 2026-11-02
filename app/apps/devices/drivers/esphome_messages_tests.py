from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from structlog.testing import capture_logs

from apps.core.stubs import MonotonicClockStub, WallClockStub
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.domain.readings import Phase, PhaseCurrents, TemperatureReading
from apps.devices.drivers.esphome_messages import EsphomeMessageHandler, PhaseMeterSink, TemperatureSink
from apps.devices.factories import DeviceFactory
from apps.devices.services.phase_meter_store import PhaseMeterStore
from apps.devices.services.state_store import TemperatureSnapshot, TemperatureStateStore

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
KITCHEN_TEMPERATURE = 'teplo/teplo-air-kitchen/sensor/temperature/state'
KITCHEN_HUMIDITY = 'teplo/teplo-air-kitchen/sensor/humidity/state'
KITCHEN_STATUS = 'teplo/teplo-air-kitchen/status'


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
def f_handler(
    f_temperature_store: TemperatureStateStore,
    f_phase_store: PhaseMeterStore,
    wall_clock: WallClockStub,
) -> EsphomeMessageHandler:
    temperature = TemperatureSink(store=f_temperature_store)
    return EsphomeMessageHandler(
        sinks={'air-kitchen': temperature, 'outdoor': temperature, 'phase-meter': PhaseMeterSink(store=f_phase_store)},
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
def f_routed_handler() -> EsphomeMessageHandler:
    DeviceFactory(slug='air-kitchen', kind=DeviceKind.TEMPERATURE_SENSOR, driver=DeviceDriver.ESPHOME_TEMPERATURE)
    DeviceFactory(slug='phase-meter', kind=DeviceKind.PHASE_METER, driver=DeviceDriver.ESPHOME_PHASE_METER)
    DeviceFactory(slug='air-attic', kind=DeviceKind.TEMPERATURE_SENSOR, driver=DeviceDriver.UNSUPPORTED)
    DeviceFactory(slug='thermostat-kitchen')
    return EsphomeMessageHandler.init()


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
