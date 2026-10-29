from decimal import Decimal

import pytest

from apps.climate.domain.modes import ScheduleMode, ZoneKind
from apps.panel.usecases.get_panel_overview import GetPanelOverviewOutputDTO, ZoneOverview
from apps.system.domain.clock import ClockStatus


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
                error=None,
                floor_temperature=Decimal('24.5'),
                power=True,
                setpoint=Decimal('28.0'),
                target_setpoint=Decimal('28.0'),
                boosted=False,
                live_control=False,
            ),
        ),
        clock=ClockStatus(trusted=True, reason='ntp synchronized'),
        dry_run=True,
    )
