from decimal import Decimal

from apps.core.domain.errors import DomainError
from apps.devices.domain.capabilities import FloorThermostat
from apps.devices.domain.errors import DeviceUnavailableError, DriverNotImplementedError
from apps.devices.domain.readings import ThermostatMode, ThermostatState


class FloorThermostatStub:
    def __init__(self, state: ThermostatState | None, error: DomainError | None = None) -> None:
        self._state = state
        self._error = error
        self._commands: list[tuple[str, object]] = []

    @property
    def commands(self) -> list[tuple[str, object]]:
        return self._commands

    def read_state(self) -> ThermostatState:
        if self._error is not None:
            raise self._error

        if self._state is None:
            raise DeviceUnavailableError('no state configured')

        return self._state

    def set_power(self, power: bool) -> None:
        self._command('power', power)

    def set_mode(self, mode: ThermostatMode) -> None:
        self._command('mode', mode)

    def set_setpoint(self, setpoint: Decimal) -> None:
        self._command('setpoint', setpoint)

    def _command(self, name: str, value: object) -> None:
        if self._error is not None:
            raise self._error

        self._commands.append((name, value))


class ThermostatProviderStub:
    def __init__(self, thermostats: dict[str, FloorThermostat]) -> None:
        self._thermostats = thermostats

    def thermostat(self, slug: str) -> FloorThermostat:
        if slug not in self._thermostats:
            raise DriverNotImplementedError(slug)

        return self._thermostats[slug]
