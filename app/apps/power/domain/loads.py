from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from apps.devices.domain.readings import Phase


class LoadKind(StrEnum):
    ZONE = 'zone'
    PLUG = 'plug'


class LoadPhase(StrEnum):
    L1 = 'l1'
    L2 = 'l2'
    L3 = 'l3'
    UNKNOWN = 'unknown'


MEASURED_PHASES = (LoadPhase.L1, LoadPhase.L2, LoadPhase.L3)
METER_PHASES = {Phase.A: LoadPhase.L1, Phase.B: LoadPhase.L2, Phase.C: LoadPhase.L3}


class PhaseOrigin(StrEnum):
    SET = 'set'
    LEARNED = 'learned'
    UNKNOWN = 'unknown'


class AmpsOrigin(StrEnum):
    NOMINAL = 'nominal'
    LEARNED = 'learned'
    DEFAULT = 'default'


class ShedReason(StrEnum):
    OVERLOAD = 'overload'
    METER_LOST = 'meter_lost'


@dataclass(frozen=True, kw_only=True)
class LearnedPhase:
    phase: LoadPhase
    amps: Decimal
    samples: int


@dataclass(frozen=True, kw_only=True)
class LiveShed:
    at: datetime
    reason: ShedReason
    phase: LoadPhase
    was_on: bool | None


@dataclass(frozen=True, kw_only=True)
class LoadRecord:
    slug: str
    name: str
    kind: LoadKind
    zone_slug: str | None
    device_slug: str
    priority: int
    phase: LoadPhase
    nominal_amps: Decimal | None
    learned: LearnedPhase | None
    live: bool
    shed: LiveShed | None
    last_command_at: datetime | None = None

    @property
    def effective_phase(self) -> LoadPhase:
        if self.phase is not LoadPhase.UNKNOWN:
            return self.phase

        return LoadPhase.UNKNOWN if self.learned is None else self.learned.phase

    @property
    def phase_origin(self) -> PhaseOrigin:
        if self.phase is not LoadPhase.UNKNOWN:
            return PhaseOrigin.SET

        return PhaseOrigin.UNKNOWN if self.learned is None else PhaseOrigin.LEARNED

    @property
    def amps_origin(self) -> AmpsOrigin:
        if self.nominal_amps is not None:
            return AmpsOrigin.NOMINAL

        return AmpsOrigin.DEFAULT if self.learned is None else AmpsOrigin.LEARNED

    def effective_amps(self, default: Decimal) -> Decimal:
        if self.nominal_amps is not None:
            return self.nominal_amps

        return default if self.learned is None else self.learned.amps


def measured_currents(amps: Mapping[Phase, Decimal]) -> dict[LoadPhase, Decimal]:
    return {METER_PHASES[phase]: current for phase, current in amps.items()}
