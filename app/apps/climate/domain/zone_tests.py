from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.climate.domain.modes import ZoneKind
from apps.climate.domain.zone import BOOST_DURATION, MANUAL_HOLD, AirTargets, FloorSetpoints, ZoneSettings

NOW = datetime(2026, 10, 29, 2, 0, tzinfo=ZoneInfo('Europe/Moscow'))


def _zone(*, boost_until: datetime | None = None, manual_until: datetime | None = None) -> ZoneSettings:
    return ZoneSettings(
        slug='kitchen',
        name='Kitchen',
        kind=ZoneKind.AIR,
        thermostat_slug='thermostat-kitchen',
        air_sensor_slug=None,
        heating_enabled=True,
        live_control=True,
        air_control_live=False,
        setpoints=FloorSetpoints(
            comfort=Decimal('28.0'),
            night=Decimal('22.0'),
            boost=Decimal('30.0'),
            idle=Decimal('18.0'),
            limit=Decimal('30.0'),
        ),
        air_targets=AirTargets(comfort=Decimal('22.0'), night=Decimal('19.5')),
        preheat_lead=timedelta(hours=3),
        windows=(),
        boost_until=boost_until,
        last_command_at=None,
        last_dry_run_at=None,
        last_air_dry_run_at=None,
        manual_until=manual_until,
    )


@pytest.fixture
def f_zone(request: pytest.FixtureRequest) -> ZoneSettings:
    return _zone(boost_until=request.param)


@pytest.fixture
def f_held_zone(request: pytest.FixtureRequest) -> ZoneSettings:
    return _zone(manual_until=request.param)


@pytest.mark.parametrize(
    ('f_zone', 'expected'),
    [
        (None, False),
        (NOW, False),
        (NOW + timedelta(hours=1), True),
        (NOW + BOOST_DURATION, True),
        (NOW + BOOST_DURATION + timedelta(seconds=1), False),
        (NOW + timedelta(days=2), False),
    ],
    indirect=['f_zone'],
)
def test_is_boosted_only_within_boost_duration_from_now(f_zone: ZoneSettings, expected: bool) -> None:
    assert f_zone.is_boosted(NOW) is expected


@pytest.mark.parametrize(
    ('f_held_zone', 'expected'),
    [
        (None, False),
        (NOW - timedelta(minutes=1), False),
        (NOW, False),
        (NOW + timedelta(hours=1), True),
        (NOW + MANUAL_HOLD, True),
        (NOW + MANUAL_HOLD + timedelta(seconds=1), False),
        (NOW + timedelta(hours=5), False),
    ],
    ids=[
        'never_held',
        'hold_over',
        'hold_ends_now',
        'within_the_hold',
        'held_just_now',
        'beyond_two_hours',
        'beyond_two_hours_of_a_clock_set_back',
    ],
    indirect=['f_held_zone'],
)
def test_zone_is_held_by_hand_only_while_its_hold_ends_within_two_hours_from_now(
    f_held_zone: ZoneSettings,
    expected: bool,
) -> None:
    assert f_held_zone.held_by_hand(NOW) is expected
