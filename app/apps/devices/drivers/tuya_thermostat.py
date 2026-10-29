import threading
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol

import tinytuya
from django.utils import timezone

from apps.core.services.secrets import DeviceSecret
from apps.devices.domain.errors import DeviceProtocolError, DeviceUnavailableError
from apps.devices.domain.readings import ThermostatMode, ThermostatState

POWER_DP = 1
MODE_DP = 2
SETPOINT_DP = 16
FLOOR_TEMPERATURE_DP = 24
TENTHS = Decimal(10)

TuyaPayload = dict[str, object]


class TuyaClient(Protocol):
    def status(self) -> TuyaPayload | None: ...

    def set_value(self, index: int, value: bool | int | str) -> TuyaPayload | None: ...


class TuyaThermostatDriver:
    def __init__(self, client: TuyaClient, clock: Callable[[], datetime]) -> None:
        self._client = client
        self._clock = clock
        self._lock = threading.Lock()

    @classmethod
    def init(cls, secret: DeviceSecret) -> 'TuyaThermostatDriver':
        client = tinytuya.Device(
            secret.device_id,
            secret.address,
            secret.local_key,
            version=float(secret.protocol),
            connection_timeout=5,
            connection_retry_limit=2,
            connection_retry_delay=1,
        )
        return cls(client=client, clock=timezone.now)

    def read_state(self) -> ThermostatState:
        try:
            with self._lock:
                payload = self._client.status()
        except OSError as error:
            raise DeviceUnavailableError(str(error)) from error

        if not payload or 'Error' in payload:
            raise DeviceUnavailableError(str((payload or {}).get('Error', 'empty response')))

        return self._state(payload)

    def set_power(self, power: bool) -> None:
        self._write(POWER_DP, power)

    def set_mode(self, mode: ThermostatMode) -> None:
        self._write(MODE_DP, mode.value)

    def set_setpoint(self, setpoint: Decimal) -> None:
        self._write(SETPOINT_DP, int(setpoint * TENTHS))

    def _state(self, payload: TuyaPayload) -> ThermostatState:
        dps = payload.get('dps')
        if not isinstance(dps, dict):
            raise DeviceProtocolError(f'payload without dps: {payload}')

        try:
            return ThermostatState(
                power=bool(dps[str(POWER_DP)]),
                mode=ThermostatMode(str(dps[str(MODE_DP)])),
                setpoint=Decimal(str(dps[str(SETPOINT_DP)])) / TENTHS,
                floor_temperature=Decimal(str(dps[str(FLOOR_TEMPERATURE_DP)])) / TENTHS,
                read_at=self._clock(),
            )
        except (KeyError, ValueError, InvalidOperation) as error:
            raise DeviceProtocolError(f'unexpected thermostat payload: {dps}') from error

    def _write(self, dp: int, value: bool | int | str) -> None:
        try:
            with self._lock:
                payload = self._client.set_value(dp, value)
        except OSError as error:
            raise DeviceUnavailableError(str(error)) from error

        if payload and 'Error' in payload:
            raise DeviceUnavailableError(str(payload['Error']))
