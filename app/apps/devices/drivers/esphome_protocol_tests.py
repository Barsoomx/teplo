from decimal import Decimal

import pytest

from apps.devices.domain.errors import DeviceProtocolError, SensorReadingError
from apps.devices.domain.readings import ClimateMode, ClimateModeWord
from apps.devices.drivers.esphome_protocol import (
    ClimateStateTopic,
    NodeStatusTopic,
    ReportedMode,
    SensorStateTopic,
    climate_command_topic,
    climate_mode,
    parse_topic,
    parse_value,
)

LONG_PAYLOAD = b'x' * 40


@pytest.mark.parametrize(
    ('topic', 'expected'),
    [
        (
            'teplo/teplo-air-kitchen/sensor/temperature/state',
            SensorStateTopic(node='air-kitchen', object_id='temperature'),
        ),
        (
            'teplo/teplo-phase-meter/sensor/l3_current/state',
            SensorStateTopic(node='phase-meter', object_id='l3_current'),
        ),
        (
            'teplo/teplo-pc-office/sensor/presence/state',
            SensorStateTopic(node='pc-office', object_id='presence'),
        ),
        ('teplo/teplo-outdoor/status', NodeStatusTopic(node='outdoor')),
        (
            'teplo/teplo-ac-bedroom/climate/air_conditioner/mode/state',
            ClimateStateTopic(node='ac-bedroom', object_id='air_conditioner', attribute='mode'),
        ),
        (
            'teplo/teplo-ac-bedroom/climate/air_conditioner/target_temperature/state',
            ClimateStateTopic(node='ac-bedroom', object_id='air_conditioner', attribute='target_temperature'),
        ),
    ],
)
def test_topic_names_its_node(topic: str, expected: SensorStateTopic | NodeStatusTopic | ClimateStateTopic) -> None:
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
        'teplo/teplo-ac-bedroom/climate/air_conditioner/mode/command',
        'teplo/ac-bedroom/climate/air_conditioner/mode/state',
        'teplo/teplo-ac-bedroom/climate/air_conditioner/state',
    ],
    ids=[
        'node-without-prefix',
        'no-state-suffix',
        'command-topic',
        'other-component',
        'deeper-status',
        'other-root',
        'climate-command',
        'climate-node-without-prefix',
        'climate-without-attribute',
    ],
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


@pytest.mark.parametrize(('payload', 'expected'), [(b'0', '0'), (b'1', '1')])
def test_presence_is_read_as_0_or_1(payload: bytes, expected: str) -> None:
    assert str(parse_value('presence', payload)) == expected


@pytest.mark.parametrize(
    ('payload', 'detail'),
    [(b'2', "presence: not 0 or 1: b'2'"), (b'0.5', "presence: not 0 or 1: b'0.5'")],
    ids=['two', 'half'],
)
def test_presence_other_than_0_or_1_is_a_protocol_error(payload: bytes, detail: str) -> None:
    with pytest.raises(DeviceProtocolError) as raised:
        parse_value('presence', payload)

    assert (raised.value.error_code, raised.value.detail) == ('device_protocol_error', detail)


def test_presence_nan_is_a_failed_reading() -> None:
    with pytest.raises(SensorReadingError) as raised:
        parse_value('presence', b'nan')

    assert (raised.value.error_code, raised.value.detail) == (
        'sensor_reading_failed',
        'presence: the node reported nan',
    )


@pytest.mark.parametrize(
    ('payload', 'mode'),
    [
        (b'off', ReportedMode(mode=ClimateMode.OFF, word=None)),
        (b'heat', ReportedMode(mode=ClimateMode.HEAT, word=None)),
        (b'cool', ReportedMode(mode=ClimateMode.COOL, word=None)),
        (b'heat_cool', ReportedMode(mode=ClimateMode.AUTO, word=ClimateModeWord.HEAT_COOL)),
        (b'auto', ReportedMode(mode=ClimateMode.AUTO, word=None)),
        (b'dry', ReportedMode(mode=ClimateMode.OTHER, word=ClimateModeWord.DRY)),
        (b'fan_only', ReportedMode(mode=ClimateMode.OTHER, word=ClimateModeWord.FAN_ONLY)),
        (b'unknown', ReportedMode(mode=ClimateMode.OTHER, word=None)),
    ],
    ids=['off', 'heat', 'cool', 'heat_cool', 'auto', 'dry', 'fan_only', 'unknown'],
)
def test_climate_mode_names_the_modes_teplo_knows_keeps_the_words_it_lumps_together_and_calls_any_other_other(
    payload: bytes,
    mode: ReportedMode,
) -> None:
    assert climate_mode(payload) == mode


@pytest.mark.parametrize(
    ('payload', 'detail'),
    [(b'\xd0\xbe\xd0\xb1', "mode: not a mode: b'\\xd0\\xbe\\xd0\\xb1'"), (b'', "mode: not a mode: b''")],
    ids=['not-ascii', 'empty'],
)
def test_climate_mode_that_is_not_a_word_is_a_protocol_error(payload: bytes, detail: str) -> None:
    with pytest.raises(DeviceProtocolError) as raised:
        climate_mode(payload)

    assert (raised.value.error_code, raised.value.detail) == ('device_protocol_error', detail)


@pytest.mark.parametrize(
    ('attribute', 'topic'),
    [
        ('mode', 'teplo/teplo-ac-bedroom/climate/air_conditioner/mode/command'),
        ('target_temperature', 'teplo/teplo-ac-bedroom/climate/air_conditioner/target_temperature/command'),
    ],
)
def test_climate_command_topic_is_the_node_s_command_topic_of_the_attribute(attribute: str, topic: str) -> None:
    assert climate_command_topic('ac-bedroom', attribute) == topic
