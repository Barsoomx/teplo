from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from apps.devices.domain.errors import DeviceProtocolError, SensorReadingError
from apps.devices.domain.readings import Phase

NODE_PREFIX = 'teplo-'
SUBSCRIPTIONS = ('teplo/+/sensor/+/state', 'teplo/+/status')
NODE_ONLINE = b'online'
NODE_OFFLINE = b'offline'
TEMPERATURE = 'temperature'
HUMIDITY = 'humidity'
PHASE_CURRENTS = {'l1_current': Phase.A, 'l2_current': Phase.B, 'l3_current': Phase.C}
SHOWN_PAYLOAD_BYTES = 32


@dataclass(frozen=True, kw_only=True)
class SensorStateTopic:
    slug: str
    object_id: str


@dataclass(frozen=True, kw_only=True)
class NodeStatusTopic:
    slug: str


def parse_topic(topic: str) -> SensorStateTopic | NodeStatusTopic | None:
    match topic.split('/'):
        case ['teplo', node, 'sensor', object_id, 'state'] if node.startswith(NODE_PREFIX):
            return SensorStateTopic(slug=node.removeprefix(NODE_PREFIX), object_id=object_id)
        case ['teplo', node, 'status'] if node.startswith(NODE_PREFIX):
            return NodeStatusTopic(slug=node.removeprefix(NODE_PREFIX))
        case _:
            return None


def parse_value(object_id: str, payload: bytes) -> Decimal:
    try:
        value = Decimal(payload.decode('ascii'))
    except (UnicodeDecodeError, InvalidOperation) as error:
        raise DeviceProtocolError(f'{object_id}: not a number: {payload[:SHOWN_PAYLOAD_BYTES]!r}') from error

    if value.is_nan():
        raise SensorReadingError(f'{object_id}: the node reported nan')

    if value.is_infinite():
        raise DeviceProtocolError(f'{object_id}: not a finite number: {payload[:SHOWN_PAYLOAD_BYTES]!r}')

    return value
