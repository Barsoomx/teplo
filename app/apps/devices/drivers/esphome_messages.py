from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Protocol

import structlog
from django.utils import timezone

from apps.core.domain.errors import DomainError
from apps.devices.domain.device import EsphomeDevice
from apps.devices.domain.errors import (
    BROKER_UNAVAILABLE,
    BrokerUnavailableError,
    DeviceUnavailableError,
    SensorReadingError,
)
from apps.devices.domain.kinds import DeviceDriver
from apps.devices.domain.readings import TemperatureReading
from apps.devices.drivers.esphome_protocol import (
    CLIMATE_OBJECT_ID,
    CURRENT_TEMPERATURE,
    HUMIDITY,
    MODE,
    NODE_OFFLINE,
    NODE_ONLINE,
    NODE_PREFIX,
    PHASE_CURRENTS,
    PRESENCE,
    TARGET_TEMPERATURE,
    TEMPERATURE,
    ClimateStateTopic,
    NodeStatusTopic,
    SensorStateTopic,
    climate_mode,
    parse_topic,
    parse_value,
)
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.esphome_climate_store import EsphomeClimateStore, esphome_climate_store
from apps.devices.services.phase_meter_store import PhaseMeterStore, phase_meter_store
from apps.devices.services.presence_store import PresenceStore, presence_store
from apps.devices.services.state_store import TemperatureStateStore, temperature_state_store

logger = structlog.get_logger(__name__)


class ReadingSink(Protocol):
    def accepts(self, object_id: str) -> bool: ...

    def record(self, slug: str, object_id: str, value: Decimal, read_at: datetime) -> None: ...

    def fail(self, slug: str, object_id: str | None, error: DomainError) -> None: ...

    def clear_failure(self, slug: str, error_code: str) -> None: ...


class ClimateStateSink(Protocol):
    def accepts_climate(self, object_id: str) -> bool: ...

    def record_climate(self, slug: str, attribute: str, payload: bytes, read_at: datetime) -> None: ...

    def fail(self, slug: str, error: DomainError) -> None: ...

    def clear_failure(self, slug: str, error_code: str) -> None: ...


class TemperatureSink:
    def __init__(self, store: TemperatureStateStore) -> None:
        self._store = store

    def accepts(self, object_id: str) -> bool:
        return object_id in (TEMPERATURE, HUMIDITY)

    def record(self, slug: str, object_id: str, value: Decimal, read_at: datetime) -> None:
        if object_id == HUMIDITY:
            self._store.amend_state(slug, lambda reading: replace(reading, humidity=value))
            return

        previous = self._store.snapshot(slug).state
        self._store.record_state(
            slug,
            TemperatureReading(
                temperature=value,
                humidity=None if previous is None else previous.humidity,
                read_at=read_at,
            ),
        )

    def fail(self, slug: str, object_id: str | None, error: DomainError) -> None:
        if object_id == HUMIDITY:
            self._store.amend_state(slug, lambda reading: replace(reading, humidity=None))
        self._store.record_failure(slug, error_code=error.error_code, error=error.detail)

    def clear_failure(self, slug: str, error_code: str) -> None:
        self._store.clear_failure(slug, error_code)


class PhaseMeterSink:
    def __init__(self, store: PhaseMeterStore) -> None:
        self._store = store

    def accepts(self, object_id: str) -> bool:
        return object_id in PHASE_CURRENTS

    def record(self, slug: str, object_id: str, value: Decimal, read_at: datetime) -> None:
        self._store.record_current(slug, PHASE_CURRENTS[object_id], value, read_at)

    def fail(self, slug: str, object_id: str | None, error: DomainError) -> None:  # noqa: ARG002
        self._store.record_failure(slug, error_code=error.error_code, error=error.detail)

    def clear_failure(self, slug: str, error_code: str) -> None:
        self._store.clear_failure(slug, error_code)


class PresenceSink:
    def __init__(self, store: PresenceStore) -> None:
        self._store = store

    def accepts(self, object_id: str) -> bool:
        return object_id == PRESENCE

    def record(self, slug: str, object_id: str, value: Decimal, read_at: datetime) -> None:  # noqa: ARG002
        self._store.record(slug, present=value == 1, read_at=read_at)

    def fail(self, slug: str, object_id: str | None, error: DomainError) -> None:  # noqa: ARG002
        self._store.record_failure(slug, error_code=error.error_code, error=error.detail)

    def clear_failure(self, slug: str, error_code: str) -> None:
        self._store.clear_failure(slug, error_code)


class EsphomeClimateSink:
    def __init__(self, store: EsphomeClimateStore) -> None:
        self._store = store

    def accepts_climate(self, object_id: str) -> bool:
        return object_id == CLIMATE_OBJECT_ID

    def record_climate(self, slug: str, attribute: str, payload: bytes, read_at: datetime) -> None:
        if attribute == MODE:
            self._store.record_mode(slug, climate_mode(payload), read_at)
        elif attribute == TARGET_TEMPERATURE:
            self._store.record_target(slug, parse_value(attribute, payload), read_at)
        elif attribute == CURRENT_TEMPERATURE:
            self._store.record_room_temperature(slug, _room_temperature(payload))

    def fail(self, slug: str, error: DomainError) -> None:
        self._store.record_failure(slug, error_code=error.error_code, error=error.detail)

    def clear_failure(self, slug: str, error_code: str) -> None:
        self._store.clear_failure(slug, error_code)


@dataclass(frozen=True, kw_only=True)
class NodeDevice:
    slug: str
    sink: ReadingSink | None = None
    climate: ClimateStateSink | None = None


class EsphomeMessageHandler:
    def __init__(self, nodes: Mapping[str, tuple[NodeDevice, ...]], clock: Callable[[], datetime]) -> None:
        self._nodes = nodes
        self._clock = clock
        self._unknown_nodes: set[str] = set()
        self._failing: set[tuple[str, str]] = set()

    @classmethod
    def init(cls) -> 'EsphomeMessageHandler':
        sinks: dict[DeviceDriver, ReadingSink] = {
            DeviceDriver.ESPHOME_TEMPERATURE: TemperatureSink(store=temperature_state_store),
            DeviceDriver.ESPHOME_PHASE_METER: PhaseMeterSink(store=phase_meter_store),
            DeviceDriver.ESPHOME_PRESENCE: PresenceSink(store=presence_store),
        }
        climate = EsphomeClimateSink(store=esphome_climate_store)
        nodes: dict[str, list[NodeDevice]] = {}
        for device in DeviceRepository().esphome_devices():
            nodes.setdefault(device.node, []).append(_node_device(device, sinks, climate))
        return cls(nodes={node: tuple(devices) for node, devices in nodes.items()}, clock=timezone.now)

    def receive(self, topic: str, payload: bytes, *, retained: bool) -> None:
        if retained:
            return

        match parse_topic(topic):
            case SensorStateTopic(node=node, object_id=object_id):
                self._reading(node, object_id, payload)
            case ClimateStateTopic(node=node, object_id=object_id, attribute=attribute):
                self._climate(node, object_id, attribute, payload)
            case NodeStatusTopic(node=node):
                self._status(node, payload)

    def connection_made(self) -> None:
        for device in self._devices():
            _clear_failure(device, BROKER_UNAVAILABLE)

    def connection_lost(self, detail: str) -> None:
        error = BrokerUnavailableError(detail)
        for device in self._devices():
            _fail(device, error)

    def _reading(self, node: str, object_id: str, payload: bytes) -> None:
        sinks = [
            (device.slug, device.sink)
            for device in self._node(node)
            if device.sink is not None and device.sink.accepts(object_id)
        ]
        if not sinks:
            return

        try:
            value = parse_value(object_id, payload)
        except DomainError as error:
            for slug, sink in sinks:
                sink.fail(slug, object_id, error)
                self._report_failure(slug, object_id, error)
            return

        read_at = self._clock()
        for slug, sink in sinks:
            sink.record(slug, object_id, value, read_at)
            self._failing.discard((slug, object_id))

    def _climate(self, node: str, object_id: str, attribute: str, payload: bytes) -> None:
        read_at = self._clock()
        for device in self._node(node):
            climate = device.climate
            if climate is None or not climate.accepts_climate(object_id):
                continue

            try:
                climate.record_climate(device.slug, attribute, payload, read_at)
            except DomainError as error:
                climate.fail(device.slug, error)
                self._report_failure(device.slug, attribute, error)
                continue

            self._failing.discard((device.slug, attribute))

    def _status(self, node: str, payload: bytes) -> None:
        for device in self._node(node):
            if payload == NODE_OFFLINE:
                _fail(device, DeviceUnavailableError('the node went offline'))
                logger.warning('esphome_node_offline', device=device.slug)
            elif payload == NODE_ONLINE:
                logger.info('esphome_node_online', device=device.slug)

    def _node(self, node: str) -> tuple[NodeDevice, ...]:
        devices = self._nodes.get(node, ())
        if not devices and node not in self._unknown_nodes:
            self._unknown_nodes.add(node)
            logger.info('esphome_node_unknown', node=f'{NODE_PREFIX}{node}')
        return devices

    def _devices(self) -> Iterator[NodeDevice]:
        return (device for devices in self._nodes.values() for device in devices)

    def _report_failure(self, slug: str, object_id: str, error: DomainError) -> None:
        if (slug, object_id) in self._failing:
            return

        self._failing.add((slug, object_id))
        logger.warning(
            'esphome_reading_failed',
            device=slug,
            object_id=object_id,
            error_code=error.error_code,
            detail=error.detail,
        )


def _node_device(
    device: EsphomeDevice,
    sinks: Mapping[DeviceDriver, ReadingSink],
    climate: ClimateStateSink,
) -> NodeDevice:
    if device.driver is DeviceDriver.ESPHOME_CLIMATE:
        return NodeDevice(slug=device.slug, climate=climate)

    return NodeDevice(slug=device.slug, sink=sinks[device.driver])


def _fail(device: NodeDevice, error: DomainError) -> None:
    if device.sink is not None:
        device.sink.fail(device.slug, None, error)
    if device.climate is not None:
        device.climate.fail(device.slug, error)


def _clear_failure(device: NodeDevice, error_code: str) -> None:
    if device.sink is not None:
        device.sink.clear_failure(device.slug, error_code)
    if device.climate is not None:
        device.climate.clear_failure(device.slug, error_code)


def _room_temperature(payload: bytes) -> Decimal | None:
    try:
        return parse_value(CURRENT_TEMPERATURE, payload)
    except SensorReadingError:
        return None
