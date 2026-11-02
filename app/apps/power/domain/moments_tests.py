from datetime import timedelta

import pytest

from apps.power.domain.moments import elapsed, held, held_since

INTERVAL = timedelta(seconds=30)


@pytest.mark.parametrize(
    ('since', 'at', 'expected'),
    [(None, 100.0, True), (100.0, 129.9, False), (100.0, 130.0, True)],
    ids=['never', 'within_the_interval', 'after_the_interval'],
)
def test_elapsed_once_the_interval_has_passed_since_the_moment(since: float | None, at: float, expected: bool) -> None:
    assert elapsed(since, at, INTERVAL) is expected


@pytest.mark.parametrize(
    ('since', 'at', 'expected'),
    [(None, 100.0, False), (100.0, 129.9, False), (100.0, 130.0, True)],
    ids=['not_holding', 'not_yet', 'for_the_hold'],
)
def test_held_once_a_condition_has_lasted_the_hold(since: float | None, at: float, expected: bool) -> None:
    assert held(since, at, INTERVAL) is expected


@pytest.mark.parametrize(
    ('since', 'holds', 'expected'),
    [(None, True, 130.0), (100.0, True, 100.0), (100.0, False, None)],
    ids=['starts_holding', 'keeps_holding', 'stops_holding'],
)
def test_held_since_keeps_the_first_moment_while_the_condition_holds(
    since: float | None,
    holds: bool,
    expected: float | None,
) -> None:
    assert held_since(since, holds, 130.0) == expected
