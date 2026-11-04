import threading
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

from django.utils import timezone

from apps.core.services.secrets import DeviceSecret
from apps.devices.domain.errors import DeviceProtocolError, DeviceUnavailableError
from apps.devices.domain.readings import ClimateMode, ClimateUnitState
from apps.devices.drivers.tuya_client import TuyaClient, TuyaPayload, tuya_client

POWER_DP = 1
SETPOINT_DP = 3
TEMPERATURE_DP = 4
LEVEL_DP = 7


class TuyaHeaterDriver:
    def __init__(self, client: TuyaClient, clock: Callable[[], datetime]) -> None:
        self._client = client
        self._clock = clock
        self._lock = threading.Lock()

    @classmethod
    def init(cls, secret: DeviceSecret) -> 'TuyaHeaterDriver':
        return cls(client=tuya_client(secret), clock=timezone.now)

    def read_state(self) -> ClimateUnitState:
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

    def set_setpoint(self, setpoint: Decimal) -> None:
        if setpoint != setpoint.to_integral_value():
            raise DeviceProtocolError(f'the heater takes whole degrees, not {setpoint}')

        degrees = int(setpoint)
        if _echoes(self._write(SETPOINT_DP, degrees), SETPOINT_DP, degrees):
            return

        if self.read_state().target != setpoint:
            raise DeviceProtocolError(f'the heater did not take the setpoint {degrees}')

    def _state(self, payload: TuyaPayload) -> ClimateUnitState:
        dps = payload.get('dps')
        if not isinstance(dps, dict):
            raise DeviceProtocolError(f'payload without dps: {payload}')

        try:
            return ClimateUnitState(
                mode=ClimateMode.HEAT if _power(dps[str(POWER_DP)]) else ClimateMode.OFF,
                target=_degrees(dps[str(SETPOINT_DP)]),
                room_temperature=_degrees(dps[str(TEMPERATURE_DP)]),
                read_at=self._clock(),
                level=_level(dps.get(str(LEVEL_DP))),
            )
        except (KeyError, ValueError) as error:
            raise DeviceProtocolError(f'unexpected heater payload: {dps}') from error

    def _write(self, dp: int, value: bool | int) -> TuyaPayload | None:
        try:
            with self._lock:
                payload = self._client.set_value(dp, value)
        except OSError as error:
            raise DeviceUnavailableError(str(error)) from error

        if payload and 'Error' in payload:
            raise DeviceUnavailableError(str(payload['Error']))

        return payload


def _echoes(payload: TuyaPayload | None, dp: int, value: int) -> bool:
    dps = None if payload is None else payload.get('dps')
    return isinstance(dps, dict) and dps.get(str(dp)) == value


def _power(value: object) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f'power is not a bool: {value!r}')

    return value


def _degrees(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f'temperature is not an integer: {value!r}')

    return Decimal(value)


def _level(value: object) -> str | None:
    return value if isinstance(value, str) else None
