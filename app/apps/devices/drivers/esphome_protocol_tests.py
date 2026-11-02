from decimal import Decimal

import pytest

from apps.devices.domain.errors import DeviceProtocolError, SensorReadingError
from apps.devices.drivers.esphome_protocol import NodeStatusTopic, SensorStateTopic, parse_topic, parse_value

LONG_PAYLOAD = b'x' * 40


@pytest.mark.parametrize(
    ('topic', 'expected'),
    [
        (
            'teplo/teplo-air-kitchen/sensor/temperature/state',
            SensorStateTopic(slug='air-kitchen', object_id='temperature'),
        ),
        (
            'teplo/teplo-phase-meter/sensor/l3_current/state',
            SensorStateTopic(slug='phase-meter', object_id='l3_current'),
        ),
        ('teplo/teplo-outdoor/status', NodeStatusTopic(slug='outdoor')),
    ],
)
def test_topic_names_the_device_by_its_node(topic: str, expected: SensorStateTopic | NodeStatusTopic) -> None:
    assert parse_topic(topic) == expected


@pytest.mark.parametrize(
    'topic',
    [
        'teplo/air-kitchen/sensor/temperature/state',
        'teplo/teplo-air-kitchen/sensor/temperature',
        'teplo/teplo-air-kitchen/sensor/temperature/command',
        'teplo/teplo-air-kitchen/binary_sensor/window/state',
        'teplo/teplo-air-kitchen/status/extra',
        'home/teplo-air-kitchen/status',
    ],
    ids=['node-without-prefix', 'no-state-suffix', 'command-topic', 'other-component', 'deeper-status', 'other-root'],
)
def test_topic_outside_the_contract_is_not_parsed(topic: str) -> None:
    assert parse_topic(topic) is None


@pytest.mark.parametrize(
    ('payload', 'expected'),
    [(b'21.5', '21.5'), (b'-3.0', '-3.0'), (b'0.10', '0.10'), (b' 41.2\n', '41.2')],
)
def test_value_is_read_as_a_decimal(payload: bytes, expected: str) -> None:
    value = parse_value('temperature', payload)

    assert (type(value), str(value)) == (Decimal, expected)


@pytest.mark.parametrize('payload', [b'nan', b'NaN', b'-nan'])
def test_nan_is_a_failed_reading(payload: bytes) -> None:
    with pytest.raises(SensorReadingError) as raised:
        parse_value('temperature', payload)

    assert (raised.value.error_code, raised.value.detail) == (
        'sensor_reading_failed',
        'temperature: the node reported nan',
    )


@pytest.mark.parametrize(
    ('payload', 'detail'),
    [
        (b'', "humidity: not a number: b''"),
        (b'21,5', "humidity: not a number: b'21,5'"),
        (b'None', "humidity: not a number: b'None'"),
        (b'\xff', "humidity: not a number: b'\\xff'"),
        (LONG_PAYLOAD, f'humidity: not a number: {LONG_PAYLOAD[:32]!r}'),
        (b'inf', "humidity: not a finite number: b'inf'"),
    ],
    ids=['empty', 'decimal-comma', 'none', 'not-ascii', 'long', 'infinity'],
)
def test_unreadable_payload_is_a_protocol_error(payload: bytes, detail: str) -> None:
    with pytest.raises(DeviceProtocolError) as raised:
        parse_value('humidity', payload)

    assert (raised.value.error_code, raised.value.detail) == ('device_protocol_error', detail)
