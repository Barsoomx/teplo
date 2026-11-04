from decimal import Decimal

from apps.core.domain.errors import DomainError
from apps.devices.domain.capabilities import (
    ClimateUnit,
    ControllableClimateUnit,
    FloorThermostat,
    MeteredPlug,
    PresenceSensor,
    SwitchablePlug,
)
from apps.devices.domain.errors import (
    ClimateUnitNotControllableError,
    DeviceUnavailableError,
    DriverNotImplementedError,
    PlugNotSwitchableError,
)
from apps.devices.domain.readings import ClimateUnitState, PlugState, PresenceReading, ThermostatMode, ThermostatState


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


class ChangingThermostatStub(FloorThermostatStub):
    def __init__(self, readings: tuple[ThermostatState | DomainError, ...]) -> None:
        super().__init__(state=None)
        self._readings = readings
        self._reads = 0

    def read_state(self) -> ThermostatState:
        reading = self._readings[min(self._reads, len(self._readings) - 1)]
        self._reads += 1
        if isinstance(reading, DomainError):
            raise reading

        return reading


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
    def __init__(
        self,
        state: PlugState | None,
        error: DomainError | None = None,
        switch_errors: tuple[DomainError, ...] = (),
    ) -> None:
        super().__init__(state=state, error=error)
        self._switch_errors = switch_errors
        self._failed_switches = 0
        self._switches: list[bool] = []

    @property
    def switches(self) -> list[bool]:
        return self._switches

    def set_on(self, on: bool) -> None:
        if self._error is not None:
            raise self._error

        if self._failed_switches < len(self._switch_errors):
            self._failed_switches += 1
            raise self._switch_errors[self._failed_switches - 1]

        self._switches.append(on)


class PlugProviderStub:
    def __init__(self, plugs: dict[str, MeteredPlug]) -> None:
        self._plugs = plugs

    def plug(self, slug: str) -> MeteredPlug:
        if slug not in self._plugs:
            raise DriverNotImplementedError(slug)

        return self._plugs[slug]


class SwitchablePlugProviderStub:
    def __init__(self, plugs: dict[str, SwitchablePlug], errors: dict[str, Exception] | None = None) -> None:
        self._plugs = plugs
        self._errors = errors
        self._asked: list[str] = []

    @property
    def asked(self) -> list[str]:
        return self._asked

    def switchable_plug(self, slug: str) -> SwitchablePlug:
        self._asked.append(slug)
        error = None if self._errors is None else self._errors.get(slug)
        if error is not None:
            raise error

        if slug not in self._plugs:
            raise PlugNotSwitchableError(slug, 'no switching driver')

        return self._plugs[slug]


class ClimateUnitStub:
    def __init__(self, state: ClimateUnitState | None, error: DomainError | None = None) -> None:
        self._state = state
        self._error = error

    def read_state(self) -> ClimateUnitState:
        if self._error is not None:
            raise self._error

        if self._state is None:
            raise DeviceUnavailableError('no state configured')

        return self._state


class ChangingClimateUnitStub(ClimateUnitStub):
    def __init__(self, readings: tuple[ClimateUnitState | DomainError, ...]) -> None:
        super().__init__(state=None)
        self._readings = readings
        self._reads = 0

    def read_state(self) -> ClimateUnitState:
        reading = self._readings[min(self._reads, len(self._readings) - 1)]
        self._reads += 1
        if isinstance(reading, DomainError):
            raise reading

        return reading


class ControllableClimateUnitStub(ClimateUnitStub):
    def __init__(
        self,
        state: ClimateUnitState | None,
        error: DomainError | None = None,
        command_errors: tuple[DomainError | None, ...] = (),
    ) -> None:
        super().__init__(state=state, error=error)
        self._command_errors = command_errors
        self._tried_commands = 0
        self._commands: list[tuple[str, object]] = []

    @property
    def commands(self) -> list[tuple[str, object]]:
        return self._commands

    def set_power(self, power: bool) -> None:
        self._command('power', power)

    def set_setpoint(self, setpoint: Decimal) -> None:
        self._command('setpoint', setpoint)

    def _command(self, name: str, value: object) -> None:
        tried = self._tried_commands
        self._tried_commands += 1
        error = self._command_errors[tried] if tried < len(self._command_errors) else None
        if error is not None:
            raise error

        self._commands.append((name, value))


class ClimateUnitProviderStub:
    def __init__(self, units: dict[str, ClimateUnit]) -> None:
        self._units = units

    def climate_unit(self, slug: str) -> ClimateUnit:
        if slug not in self._units:
            raise DriverNotImplementedError(slug)

        return self._units[slug]


class ControllableClimateUnitProviderStub:
    def __init__(self, units: dict[str, ControllableClimateUnit]) -> None:
        self._units = units
        self._asked: list[str] = []

    @property
    def asked(self) -> list[str]:
        return self._asked

    def controllable_climate_unit(self, slug: str) -> ControllableClimateUnit:
        self._asked.append(slug)
        if slug not in self._units:
            raise ClimateUnitNotControllableError(slug, 'no controlling driver')

        return self._units[slug]


class PresenceSensorStub:
    def __init__(self, reading: PresenceReading | None, error: DomainError | None = None) -> None:
        self._reading = reading
        self._error = error

    def read_presence(self) -> PresenceReading:
        if self._error is not None:
            raise self._error

        if self._reading is None:
            raise DeviceUnavailableError('no presence configured')

        return self._reading


class PresenceSensorProviderStub:
    def __init__(self, sensors: dict[str, PresenceSensor]) -> None:
        self._sensors = sensors

    def presence_sensor(self, slug: str) -> PresenceSensor:
        if slug not in self._sensors:
            raise DriverNotImplementedError(slug)

        return self._sensors[slug]


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
