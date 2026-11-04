from datetime import UTC, datetime, timedelta

import pytest

from apps.devices.domain.readings import PresenceReading
from apps.presence.domain.occupancy import (
    UNAVAILABLE,
    Occupancy,
    PresenceEvidence,
    decide_occupancy,
    evidence_of,
)

VACANT_AFTER = timedelta(minutes=20)
WATCHED = timedelta(hours=1)


def _seen(minutes: float, *, watched: timedelta = WATCHED) -> PresenceEvidence:
    return PresenceEvidence(available=True, seen_ago=timedelta(minutes=minutes), watched_for=watched)


def _watched(minutes: float) -> PresenceEvidence:
    return PresenceEvidence(available=True, seen_ago=None, watched_for=timedelta(minutes=minutes))


@pytest.mark.parametrize(
    ('evidence', 'occupancy'),
    [
        ((), Occupancy.UNKNOWN),
        ((UNAVAILABLE, UNAVAILABLE), Occupancy.UNKNOWN),
        ((_seen(3),), Occupancy.OCCUPIED),
        ((_seen(20),), Occupancy.OCCUPIED),
        ((_seen(20.5),), Occupancy.VACANT),
        ((_watched(20),), Occupancy.VACANT),
        ((_watched(19),), Occupancy.UNKNOWN),
        ((_seen(25, watched=timedelta(minutes=10)),), Occupancy.UNKNOWN),
        ((_seen(2), _watched(60)), Occupancy.OCCUPIED),
        ((_watched(60), _seen(2)), Occupancy.OCCUPIED),
        ((PresenceEvidence(available=False, seen_ago=timedelta(minutes=1)), _watched(60)), Occupancy.VACANT),
        ((PresenceEvidence(available=False, seen_ago=timedelta(minutes=1)),), Occupancy.UNKNOWN),
        ((UNAVAILABLE, _watched(5)), Occupancy.UNKNOWN),
    ],
    ids=[
        'no_sources',
        'all_unavailable',
        'someone_inside_the_window',
        'someone_at_the_edge_of_the_window',
        'someone_just_outside_the_window',
        'watched_for_the_window_without_anyone',
        'watched_less_than_the_window',
        'sighting_outside_the_window_watched_less',
        'occupied_and_vacant_sources',
        'vacant_and_occupied_sources',
        'sighting_of_an_unavailable_source_ignored',
        'only_an_unavailable_sighting',
        'one_source_back_for_minutes',
    ],
)
def test_room_is_occupied_vacant_or_unknown_by_its_available_sources(
    evidence: tuple[PresenceEvidence, ...],
    occupancy: Occupancy,
) -> None:
    assert decide_occupancy(evidence, VACANT_AFTER) is occupancy


def test_sighting_before_an_outage_counts_once_the_source_reports_again() -> None:
    back = PresenceEvidence(available=True, seen_ago=timedelta(minutes=12), watched_for=timedelta(0))

    assert decide_occupancy((back,), VACANT_AFTER) is Occupancy.OCCUPIED


def test_evidence_of_a_reading_is_available_with_its_sighting_and_watch() -> None:
    reading = PresenceReading(
        present=False,
        seen_ago=timedelta(minutes=4),
        watched_for=timedelta(minutes=30),
        read_at=datetime(2026, 11, 5, 9, 0, tzinfo=UTC),
    )

    assert evidence_of(reading) == PresenceEvidence(
        available=True,
        seen_ago=timedelta(minutes=4),
        watched_for=timedelta(minutes=30),
    )
