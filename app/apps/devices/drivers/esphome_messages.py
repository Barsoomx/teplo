from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from typing import Protocol

import structlog
from django.utils import timezone

from apps.core.domain.errors import DomainError
from apps.devices.domain.errors import BROKER_UNAVAILABLE, BrokerUnavailableError, DeviceUnavailableError
from apps.devices.domain.kinds import DeviceDriver
from apps.devices.domain.readings import TemperatureReading
from apps.devices.drivers.esphome_protocol import (
    HUMIDITY,
    NODE_OFFLINE,
    NODE_ONLINE,
    NODE_PREFIX,
    PHASE_CURRENTS,
    TEMPERATURE,
    NodeStatusTopic,
    SensorStateTopic,
    parse_topic,
    parse_value,
)
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.phase_meter_store import PhaseMeterStore, phase_meter_store
from apps.devices.services.state_store import TemperatureStateStore, temperature_state_store

logger = structlog.get_logger(__name__)


class ReadingSink(Protocol):
    def accepts(self, object_id: str) -> bool: ...

    def record(self, slug: str, object_id: str, value: Decimal, read_at: datetime) -> None: ...

    def fail(self, slug: str, object_id: str | None, error: DomainError) -> None: ...

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


class EsphomeMessageHandler:
    def __init__(self, sinks: dict[str, ReadingSink], clock: Callable[[], datetime]) -> None:
        self._sinks = sinks
        self._clock = clock
        self._unknown_nodes: set[str] = set()
        self._failing: set[tuple[str, str]] = set()

    @classmethod
    def init(cls) -> 'EsphomeMessageHandler':
        devices = DeviceRepository()
        temperature = TemperatureSink(store=temperature_state_store)
        phase_meter = PhaseMeterSink(store=phase_meter_store)
        sinks: dict[str, ReadingSink] = {
            **dict.fromkeys(devices.slugs_with_driver(DeviceDriver.ESPHOME_TEMPERATURE), temperature),
            **dict.fromkeys(devices.slugs_with_driver(DeviceDriver.ESPHOME_PHASE_METER), phase_meter),
        }
        return cls(sinks=sinks, clock=timezone.now)

    def receive(self, topic: str, payload: bytes, *, retained: bool) -> None:
        if retained:
            return

        match parse_topic(topic):
            case SensorStateTopic(slug=slug, object_id=object_id):
                self._reading(slug, object_id, payload)
            case NodeStatusTopic(slug=slug):
                self._status(slug, payload)

    def connection_made(self) -> None:
        for slug, sink in self._sinks.items():
            sink.clear_failure(slug, BROKER_UNAVAILABLE)

    def connection_lost(self, detail: str) -> None:
        error = BrokerUnavailableError(detail)
        for slug, sink in self._sinks.items():
            sink.fail(slug, None, error)

    def _reading(self, slug: str, object_id: str, payload: bytes) -> None:
        sink = self._sink(slug)
        if sink is None or not sink.accepts(object_id):
            return

        try:
            value = parse_value(object_id, payload)
        except DomainError as error:
            sink.fail(slug, object_id, error)
            self._report_failure(slug, object_id, error)
            return

        sink.record(slug, object_id, value, self._clock())
        self._failing.discard((slug, object_id))

    def _status(self, slug: str, payload: bytes) -> None:
        sink = self._sink(slug)
        if sink is None:
            return

        if payload == NODE_OFFLINE:
            sink.fail(slug, None, DeviceUnavailableError('the node went offline'))
            logger.warning('esphome_node_offline', device=slug)
        elif payload == NODE_ONLINE:
            logger.info('esphome_node_online', device=slug)

    def _sink(self, slug: str) -> ReadingSink | None:
        sink = self._sinks.get(slug)
        if sink is None and slug not in self._unknown_nodes:
            self._unknown_nodes.add(slug)
            logger.info('esphome_node_unknown', node=f'{NODE_PREFIX}{slug}')
        return sink

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
