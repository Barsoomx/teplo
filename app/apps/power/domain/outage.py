from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from apps.power.domain.loads import MEASURED_PHASES, LoadPhase


class OutageCause(StrEnum):
    UNMARKED = 'unmarked'
    OVERLOAD = 'overload'
    GRID = 'grid'
    UNKNOWN = 'unknown'


OUTAGE_ANSWERS = (OutageCause.OVERLOAD, OutageCause.GRID, OutageCause.UNKNOWN)
OVERLOAD_BEFORE_CUT = timedelta(minutes=2)


@dataclass(frozen=True, kw_only=True)
class PowerCut:
    boot_id: str | None
    began_at: datetime
    ended_at: datetime
    noticed_at: datetime


@dataclass(frozen=True, kw_only=True)
class PrecedingOverload:
    phase: LoadPhase
    amps: Decimal | None


@dataclass(frozen=True, kw_only=True)
class OutageRecord:
    id: int
    began_at: datetime
    ended_at: datetime
    cause: OutageCause
    cause_marked_at: datetime | None
    overload: PrecedingOverload | None = None


def outage_answer(value: str) -> OutageCause | None:
    return next((cause for cause in OUTAGE_ANSWERS if cause == value), None)


def overload_window(began_at: datetime) -> tuple[datetime, datetime]:
    return began_at - OVERLOAD_BEFORE_CUT, began_at


def preceding_overload(payload: Mapping[str, object] | None) -> PrecedingOverload | None:
    if payload is None:
        return None

    phase = next((phase for phase in MEASURED_PHASES if phase == payload.get('phase')), LoadPhase.UNKNOWN)
    return PrecedingOverload(phase=phase, amps=_amps(payload.get('amps')))


def _amps(value: object) -> Decimal | None:
    if value is None:
        return None

    try:
        amps = Decimal(str(value))
    except InvalidOperation:
        return None

    return amps if amps.is_finite() else None
