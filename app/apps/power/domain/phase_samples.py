from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from apps.power.domain.loads import LoadPhase

SAMPLE_SPAN = timedelta(minutes=2)
LEVEL_SAMPLES = 3


@dataclass(frozen=True, kw_only=True)
class PhaseSample:
    at: float
    amps: dict[LoadPhase, Decimal]


def mean_amps(samples: Sequence[PhaseSample], phase: LoadPhase) -> Decimal:
    return sum((sample.amps[phase] for sample in samples), Decimal(0)) / len(samples)


def level_at(samples: Sequence[PhaseSample], phase: LoadPhase, at: float) -> Decimal | None:
    latest = [sample for sample in samples if sample.at <= at][-LEVEL_SAMPLES:]
    if len(latest) < LEVEL_SAMPLES:
        return None

    return mean_amps(latest, phase)
