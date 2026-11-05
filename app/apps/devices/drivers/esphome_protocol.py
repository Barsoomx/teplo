from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from apps.devices.domain.errors import DeviceProtocolError, SensorReadingError
from apps.devices.domain.readings import ClimateMode, ClimateModeWord, Phase

NODE_PREFIX = 'teplo-'
SUBSCRIPTIONS = ('teplo/+/sensor/+/state', 'teplo/+/status', 'teplo/+/climate/+/+/state')
NODE_ONLINE = b'online'
NODE_OFFLINE = b'offline'
TEMPERATURE = 'temperature'
HUMIDITY = 'humidity'
PRESENCE = 'presence'
PHASE_CURRENTS = {'l1_current': Phase.A, 'l2_current': Phase.B, 'l3_current': Phase.C}
SHOWN_PAYLOAD_BYTES = 32
CLIMATE_OBJECT_ID = 'air_conditioner'
MODE = 'mode'
TARGET_TEMPERATURE = 'target_temperature'
CURRENT_TEMPERATURE = 'current_temperature'
CLIMATE_MODES = {
    'off': ClimateMode.OFF,
    'heat': ClimateMode.HEAT,
    'cool': ClimateMode.COOL,
    'heat_cool': ClimateMode.AUTO,
    'auto': ClimateMode.AUTO,
}
CLIMATE_MODE_WORDS = {word.value: word for word in ClimateModeWord}


@dataclass(frozen=True, kw_only=True)
class ReportedMode:
    mode: ClimateMode
    word: ClimateModeWord | None


@dataclass(frozen=True, kw_only=True)
class SensorStateTopic:
    node: str
    object_id: str


@dataclass(frozen=True, kw_only=True)
class NodeStatusTopic:
    node: str


@dataclass(frozen=True, kw_only=True)
class ClimateStateTopic:
    node: str
    object_id: str
    attribute: str


def parse_topic(topic: str) -> SensorStateTopic | NodeStatusTopic | ClimateStateTopic | None:
    match topic.split('/'):
        case ['teplo', node, 'sensor', object_id, 'state'] if node.startswith(NODE_PREFIX):
            return SensorStateTopic(node=node.removeprefix(NODE_PREFIX), object_id=object_id)
        case ['teplo', node, 'status'] if node.startswith(NODE_PREFIX):
            return NodeStatusTopic(node=node.removeprefix(NODE_PREFIX))
        case ['teplo', node, 'climate', object_id, attribute, 'state'] if node.startswith(NODE_PREFIX):
            return ClimateStateTopic(node=node.removeprefix(NODE_PREFIX), object_id=object_id, attribute=attribute)
        case _:
            return None


def climate_command_topic(node: str, attribute: str) -> str:
    return f'teplo/{NODE_PREFIX}{node}/climate/{CLIMATE_OBJECT_ID}/{attribute}/command'


def climate_mode(payload: bytes) -> ReportedMode:
    try:
        word = payload.decode('ascii').strip()
    except UnicodeDecodeError:
        word = ''
    if not word:
        raise DeviceProtocolError(f'{MODE}: not a mode: {payload[:SHOWN_PAYLOAD_BYTES]!r}')

    return ReportedMode(
        mode=CLIMATE_MODES.get(word, ClimateMode.OTHER),
        word=CLIMATE_MODE_WORDS.get(word),
    )


def parse_value(object_id: str, payload: bytes) -> Decimal:
    try:
        value = Decimal(payload.decode('ascii'))
    except (UnicodeDecodeError, InvalidOperation) as error:
        raise DeviceProtocolError(f'{object_id}: not a number: {payload[:SHOWN_PAYLOAD_BYTES]!r}') from error

    if value.is_nan():
        raise SensorReadingError(f'{object_id}: the node reported nan')

    if value.is_infinite():
        raise DeviceProtocolError(f'{object_id}: not a finite number: {payload[:SHOWN_PAYLOAD_BYTES]!r}')

    if object_id == PRESENCE and value not in (0, 1):
        raise DeviceProtocolError(f'presence: not 0 or 1: {payload[:SHOWN_PAYLOAD_BYTES]!r}')

    return value
