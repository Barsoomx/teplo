from decimal import Decimal

from apps.core.domain.errors import DomainError
from apps.devices.domain.capabilities import FloorThermostat, MeteredPlug, SwitchablePlug
from apps.devices.domain.errors import DeviceUnavailableError, DriverNotImplementedError, PlugNotSwitchableError
from apps.devices.domain.readings import PlugState, ThermostatMode, ThermostatState


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


class MeteredPlugStub:
    def __init__(self, state: PlugState | None, error: DomainError | None = None) -> None:
        self._state = state
        self._error = error

    def read_state(self) -> PlugState:
        if self._error is not None:
            raise self._error

        if self._state is None:
            raise DeviceUnavailableError('no state configured')

        return self._state


class SwitchablePlugStub(MeteredPlugStub):
    def __init__(self, state: PlugState | None, error: DomainError | None = None) -> None:
        super().__init__(state=state, error=error)
        self._switches: list[bool] = []

    @property
    def switches(self) -> list[bool]:
        return self._switches

    def set_on(self, on: bool) -> None:
        if self._error is not None:
            raise self._error

        self._switches.append(on)


class PlugProviderStub:
    def __init__(self, plugs: dict[str, MeteredPlug]) -> None:
        self._plugs = plugs

    def plug(self, slug: str) -> MeteredPlug:
        if slug not in self._plugs:
            raise DriverNotImplementedError(slug)

        return self._plugs[slug]


class SwitchablePlugProviderStub:
    def __init__(self, plugs: dict[str, SwitchablePlug]) -> None:
        self._plugs = plugs

    def switchable_plug(self, slug: str) -> SwitchablePlug:
        if slug not in self._plugs:
            raise PlugNotSwitchableError(slug, 'no switching driver')

        return self._plugs[slug]


class MqttReceiverStub:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self._received: list[tuple[str, bytes, bool]] = []
        self._made = 0
        self._lost: list[str] = []

    @property
    def received(self) -> list[tuple[str, bytes, bool]]:
        return self._received

    @property
    def made(self) -> int:
        return self._made

    @property
    def lost(self) -> list[str]:
        return self._lost

    def receive(self, topic: str, payload: bytes, *, retained: bool) -> None:
        if self._error is not None:
            raise self._error

        self._received.append((topic, payload, retained))

    def connection_made(self) -> None:
        self._made += 1

    def connection_lost(self, detail: str) -> None:
        self._lost.append(detail)
