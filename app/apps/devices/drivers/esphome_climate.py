import threading
from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from typing import Protocol

from apps.core.domain.errors import DomainError
from apps.devices.domain.errors import DeviceProtocolError, DeviceUnavailableError
from apps.devices.domain.readings import ClimateMode, ClimateUnitState
from apps.devices.drivers.esphome_protocol import MODE, TARGET_TEMPERATURE, climate_command_topic
from apps.devices.services.esphome_climate_store import EsphomeClimateStore, esphome_climate_store
from apps.devices.services.mqtt_listener import mqtt_listener

FRESH_FOR = timedelta(seconds=90)
ECHO_TIMEOUT = 10.0
SETPOINT_STEP = Decimal('0.5')
SWITCHED = {True: 'on', False: 'off'}


class CommandPublisher(Protocol):
    def publish(self, topic: str, payload: bytes) -> None: ...


class EsphomeClimateDriver:
    def __init__(
        self,
        slug: str,
        node: str,
        store: EsphomeClimateStore,
        publisher: CommandPublisher,
        echo_timeout: float,
    ) -> None:
        self._slug = slug
        self._node = node
        self._store = store
        self._publisher = publisher
        self._echo_timeout = echo_timeout
        self._lock = threading.Lock()

    @classmethod
    def init(cls, slug: str, node: str) -> 'EsphomeClimateDriver':
        return cls(
            slug=slug,
            node=node,
            store=esphome_climate_store,
            publisher=mqtt_listener,
            echo_timeout=ECHO_TIMEOUT,
        )

    def read_state(self) -> ClimateUnitState:
        snapshot = self._store.snapshot(self._slug)
        if snapshot.error_code is not None and snapshot.error is not None:
            raise DomainError(snapshot.error, error_code=snapshot.error_code)

        if snapshot.state is None or snapshot.age is None or snapshot.age > FRESH_FOR:
            raise DeviceUnavailableError('no recent state from the node')

        return snapshot.state

    def set_power(self, power: bool) -> None:
        mode = ClimateMode.HEAT if power else ClimateMode.OFF
        if not self._command(MODE, mode.value.encode('ascii'), lambda state: state.mode is mode):
            raise DeviceProtocolError(f'the unit did not switch {SWITCHED[power]}')

    def set_setpoint(self, setpoint: Decimal) -> None:
        if setpoint % SETPOINT_STEP:
            raise DeviceProtocolError(f'the unit takes half degrees, not {setpoint}')

        payload = f'{setpoint:.1f}'.encode('ascii')
        if not self._command(TARGET_TEMPERATURE, payload, lambda state: state.target == setpoint):
            raise DeviceProtocolError(f'the unit did not take the setpoint {setpoint}')

    def _command(self, attribute: str, payload: bytes, echoes: Callable[[ClimateUnitState], bool]) -> bool:
        with self._lock:
            reported = self._store.report_count()
            self._publisher.publish(climate_command_topic(self._node, attribute), payload)
            return self._store.wait_for(self._slug, echoes, reported_after=reported, timeout=self._echo_timeout)
