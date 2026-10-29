from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.climate.domain.modes import ZoneKind
from apps.climate.domain.zone import BOOST_DURATION, FloorSetpoints, ZoneSettings

NOW = datetime(2026, 10, 29, 2, 0, tzinfo=ZoneInfo('Europe/Moscow'))


@pytest.fixture
def f_zone(request: pytest.FixtureRequest) -> ZoneSettings:
    return ZoneSettings(
        slug='kitchen',
        name='Kitchen',
        kind=ZoneKind.AIR,
        thermostat_slug='thermostat-kitchen',
        heating_enabled=True,
        live_control=True,
        setpoints=FloorSetpoints(comfort=Decimal('28.0'), night=Decimal('22.0'), boost=Decimal('30.0')),
        preheat_lead=timedelta(hours=3),
        windows=(),
        boost_until=request.param,
        last_command_at=None,
        last_dry_run_at=None,
    )


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
