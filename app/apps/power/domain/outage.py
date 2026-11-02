from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class OutageCause(StrEnum):
    UNMARKED = 'unmarked'
    OVERLOAD = 'overload'
    GRID = 'grid'
    UNKNOWN = 'unknown'


OUTAGE_ANSWERS = (OutageCause.OVERLOAD, OutageCause.GRID, OutageCause.UNKNOWN)


@dataclass(frozen=True, kw_only=True)
class PowerCut:
    boot_id: str | None
    began_at: datetime
    ended_at: datetime
    noticed_at: datetime


@dataclass(frozen=True, kw_only=True)
class OutageRecord:
    id: int
    began_at: datetime
    ended_at: datetime
    cause: OutageCause
    cause_marked_at: datetime | None


def outage_answer(value: str) -> OutageCause | None:
    return next((cause for cause in OUTAGE_ANSWERS if cause == value), None)
