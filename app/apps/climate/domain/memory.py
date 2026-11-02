from dataclasses import dataclass
from decimal import Decimal

from apps.climate.domain.floor_policy import ThermostatTarget
from apps.climate.domain.learning import Morning
from apps.climate.domain.modes import AirState, ControlPath, ScheduleMode
from apps.climate.domain.observation import StopWatch
from apps.climate.domain.optimal_start import PreheatStart


@dataclass(frozen=True, kw_only=True)
class AirDecision:
    state: AirState
    target: ThermostatTarget


@dataclass(frozen=True, kw_only=True)
class ZoneMemory:
    path: ControlPath | None = None
    mode: ScheduleMode | None = None
    air_state: AirState | None = None
    heat_target: Decimal | None = None
    would_be: AirDecision | None = None
    preheat: PreheatStart | None = None
    morning: Morning | None = None
    stop: StopWatch | None = None

    def air_state_on(self, path: ControlPath) -> AirState | None:
        return self.air_state if path is self.path else None

    def heat_target_on(self, path: ControlPath) -> Decimal | None:
        return self.heat_target if path is self.path else None
