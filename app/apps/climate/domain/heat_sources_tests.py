from datetime import UTC, datetime, timedelta

import pytest

from apps.climate.domain.heat_sources import FireplaceRecord

NOW = datetime(2026, 11, 4, 18, 10, tzinfo=UTC)


@pytest.fixture
def f_fireplace(request: pytest.FixtureRequest) -> FireplaceRecord:
    return FireplaceRecord(
        slug='fireplace',
        name='Камин',
        room_slug='open-space',
        room_name='Гостиная, кухня и прихожая',
        live=False,
        lit_until=request.param,
    )


@pytest.mark.parametrize(
    ('f_fireplace', 'lit'),
    [
        (None, False),
        (NOW - timedelta(minutes=1), False),
        (NOW, False),
        (NOW + timedelta(minutes=1), True),
        (NOW + timedelta(hours=4), True),
        (NOW + timedelta(hours=4, minutes=1), False),
    ],
    ids=[
        'never_marked',
        'past',
        'ending_now',
        'within_four_hours',
        'four_hours_ahead',
        'beyond_after_a_clock_set_back',
    ],
    indirect=['f_fireplace'],
)
def test_fireplace_mark_counts_while_it_ends_within_four_hours(f_fireplace: FireplaceRecord, lit: bool) -> None:
    assert f_fireplace.lit(NOW) is lit
