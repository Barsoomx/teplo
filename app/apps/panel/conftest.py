from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.utils import translation

from apps.climate.domain.modes import ScheduleMode, ZoneKind
from apps.climate.domain.schedule import ScheduleTransition
from apps.panel.usecases.get_panel_overview import GetPanelOverviewOutputDTO, ZoneOverview, ZoneRegime
from apps.system.domain.clock import ClockStatus, ClockStatusCode


@pytest.fixture
def panel_overview() -> GetPanelOverviewOutputDTO:
    return GetPanelOverviewOutputDTO(
        zones=(
            ZoneOverview(
                slug='kitchen',
                name='Кухня',
                kind=ZoneKind.AIR,
                mode=ScheduleMode.COMFORT,
                available=True,
                error_code=None,
                error=None,
                floor_temperature=Decimal('24.5'),
                power=True,
                setpoint=Decimal('28.0'),
                target_setpoint=Decimal('28.0'),
                boosted=False,
                live_control=False,
                regime=ZoneRegime.COMFORT,
                boost_until=None,
                next_change=ScheduleTransition(
                    mode=ScheduleMode.NIGHT,
                    at=datetime(2026, 10, 29, 23, 30, tzinfo=ZoneInfo('Europe/Moscow')),
                ),
            ),
        ),
        clock=ClockStatus(trusted=True, code=ClockStatusCode.NTP_SYNCHRONIZED, reason='ntp synchronized'),
        dry_run=True,
    )


@pytest.fixture
def f_russian_client(f_owner: User) -> Iterator[Client]:
    client = Client(headers={'accept-language': 'ru'})
    client.force_login(f_owner)
    with translation.override('ru'):
        yield client
