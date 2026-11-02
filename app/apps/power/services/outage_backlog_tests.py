from datetime import UTC, datetime, timedelta

import pytest

from apps.power.domain.outage import PowerCut
from apps.power.services.outage_backlog import OutageBacklog

EVENING = datetime(2026, 10, 31, 17, 24, tzinfo=UTC)
EVENING_CUT = PowerCut(
    began_at=EVENING,
    ended_at=EVENING + timedelta(minutes=9),
    noticed_at=EVENING + timedelta(minutes=11),
)
NIGHT_CUT = PowerCut(
    began_at=EVENING + timedelta(hours=5),
    ended_at=EVENING + timedelta(hours=5, minutes=2),
    noticed_at=EVENING + timedelta(hours=5, minutes=4),
)
MORNING_CUT = PowerCut(
    began_at=EVENING + timedelta(hours=14),
    ended_at=EVENING + timedelta(hours=14, minutes=30),
    noticed_at=EVENING + timedelta(hours=14, minutes=31),
)


@pytest.fixture
def f_backlog() -> OutageBacklog:
    backlog = OutageBacklog()
    backlog.put(EVENING_CUT)
    backlog.put(NIGHT_CUT)
    return backlog


@pytest.fixture
def f_backlog_with_a_later_cut() -> OutageBacklog:
    backlog = OutageBacklog()
    backlog.put(MORNING_CUT)
    return backlog


def test_power_cuts_wait_in_the_order_they_came(f_backlog: OutageBacklog) -> None:
    assert f_backlog.pending() == (EVENING_CUT, NIGHT_CUT)


def test_taking_the_power_cuts_empties_the_backlog(f_backlog: OutageBacklog) -> None:
    taken = f_backlog.take()

    assert (taken, f_backlog.pending()) == ((EVENING_CUT, NIGHT_CUT), ())


def test_power_cuts_put_back_wait_before_the_ones_put_since(f_backlog_with_a_later_cut: OutageBacklog) -> None:
    f_backlog_with_a_later_cut.put_back((EVENING_CUT, NIGHT_CUT))

    assert f_backlog_with_a_later_cut.pending() == (EVENING_CUT, NIGHT_CUT, MORNING_CUT)
