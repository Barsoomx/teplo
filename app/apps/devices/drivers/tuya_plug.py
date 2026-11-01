import threading
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Protocol

import tinytuya
from django.utils import timezone

from apps.core.services.secrets import DeviceSecret
from apps.devices.domain.errors import DeviceProtocolError, DeviceUnavailableError
from apps.devices.domain.readings import PlugState

SWITCH_DP = 1
CURRENT_DP = 18
POWER_DP = 19
VOLTAGE_DP = 20
MILLIAMPS = Decimal(1000)
TENTHS = Decimal(10)

TuyaPayload = dict[str, object]


class TuyaStatusClient(Protocol):
    def status(self) -> TuyaPayload | None: ...


def plug_client(secret: DeviceSecret) -> tinytuya.Device:
    client = tinytuya.Device(
        secret.device_id,
        secret.address,
        secret.local_key,
        version=float(secret.protocol),
        connection_timeout=5,
        connection_retry_limit=2,
        connection_retry_delay=1,
    )
    client.disabledetect = True
    return client


class TuyaPlugDriver:
    def __init__(self, client: TuyaStatusClient, clock: Callable[[], datetime]) -> None:
        self._client = client
        self._clock = clock
        self._lock = threading.Lock()

    @classmethod
    def init(cls, secret: DeviceSecret) -> 'TuyaPlugDriver':
        return cls(client=plug_client(secret), clock=timezone.now)

    def read_state(self) -> PlugState:
        try:
            with self._lock:
                payload = self._client.status()
        except OSError as error:
            raise DeviceUnavailableError(str(error)) from error

        if not payload or 'Error' in payload:
            raise DeviceUnavailableError(str((payload or {}).get('Error', 'empty response')))

        return self._state(payload)

    def _state(self, payload: TuyaPayload) -> PlugState:
        dps = payload.get('dps')
        if not isinstance(dps, dict):
            raise DeviceProtocolError(f'payload without dps: {payload}')

        try:
            return PlugState(
                on=_switch(dps[str(SWITCH_DP)]),
                power_watts=_scaled(dps[str(POWER_DP)], TENTHS),
                current_amps=_scaled(dps[str(CURRENT_DP)], MILLIAMPS),
                voltage_volts=_scaled(dps[str(VOLTAGE_DP)], TENTHS),
                read_at=self._clock(),
            )
        except (KeyError, ValueError) as error:
            raise DeviceProtocolError(f'unexpected plug payload: {dps}') from error


def _switch(value: object) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f'switch is not a bool: {value!r}')

    return value


def _scaled(value: object, divisor: Decimal) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f'reading is not an integer: {value!r}')

    return Decimal(value) / divisor
