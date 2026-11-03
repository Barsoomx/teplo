from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from apps.power.domain.loads import MEASURED_PHASES, LoadPhase

Currents = Mapping[LoadPhase, Decimal]


@dataclass(frozen=True, kw_only=True)
class PhaseOverrun:
    phase: LoadPhase
    amps: Decimal


def phases_of(phase: LoadPhase) -> tuple[LoadPhase, ...]:
    return MEASURED_PHASES if phase is LoadPhase.UNKNOWN else (phase,)


def overrun(phase: LoadPhase, amps: Decimal, currents: Currents, ceiling: Decimal) -> PhaseOverrun | None:
    return next(
        (
            PhaseOverrun(phase=measured, amps=currents[measured] + amps)
            for measured in phases_of(phase)
            if measured in currents and currents[measured] + amps > ceiling
        ),
        None,
    )
