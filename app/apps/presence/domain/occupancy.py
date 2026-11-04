from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from apps.devices.domain.readings import PresenceReading


class Occupancy(StrEnum):
    OCCUPIED = 'occupied'
    VACANT = 'vacant'
    UNKNOWN = 'unknown'


KNOWN_OCCUPANCY = frozenset({Occupancy.OCCUPIED, Occupancy.VACANT})


@dataclass(frozen=True, kw_only=True)
class PresenceEvidence:
    available: bool
    seen_ago: timedelta | None = None
    watched_for: timedelta | None = None


UNAVAILABLE = PresenceEvidence(available=False)


def evidence_of(reading: PresenceReading) -> PresenceEvidence:
    return PresenceEvidence(available=True, seen_ago=reading.seen_ago, watched_for=reading.watched_for)


def decide_occupancy(evidence: Iterable[PresenceEvidence], vacant_after: timedelta) -> Occupancy:
    available = [item for item in evidence if item.available]
    if any(item.seen_ago is not None and item.seen_ago <= vacant_after for item in available):
        return Occupancy.OCCUPIED

    if any(item.watched_for is not None and item.watched_for >= vacant_after for item in available):
        return Occupancy.VACANT

    return Occupancy.UNKNOWN
