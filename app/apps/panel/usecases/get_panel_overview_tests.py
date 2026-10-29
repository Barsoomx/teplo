from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.climate.domain.modes import ScheduleMode, ZoneKind
from apps.climate.factories import ComfortPeriodFactory, ZoneFactory
from apps.climate.repositories.zone import ZoneRepository
from apps.devices.domain.readings import ThermostatMode, ThermostatState
from apps.devices.services.state_store import ThermostatStateStore
from apps.panel.usecases.get_panel_overview import (
    GetPanelOverviewInputDTO,
    GetPanelOverviewOutputDTO,
    GetPanelOverviewUseCase,
    ZoneOverview,
)
from apps.system.domain.clock import ClockStatus
from apps.system.stubs import ClockStub

pytestmark = pytest.mark.django_db

MORNING = datetime(2026, 10, 29, 7, 0, tzinfo=ZoneInfo('Europe/Moscow'))


@pytest.fixture
def f_use_case() -> GetPanelOverviewUseCase:
    zone = ZoneFactory.create(slug='kitchen', name='Кухня', thermostat__slug='thermostat-kitchen')
    ComfortPeriodFactory.create_batch(7, zone=zone, start=time(6, 30), end=time(23, 30))
    store = ThermostatStateStore(unavailable_after=timedelta(minutes=10))
    store.record_state(
        'thermostat-kitchen',
        ThermostatState(
            power=True,
            mode=ThermostatMode.HOLD,
            setpoint=Decimal('22.0'),
            floor_temperature=Decimal('21.5'),
            read_at=MORNING,
        ),
    )
    return GetPanelOverviewUseCase(zones=ZoneRepository(), store=store, clock=ClockStub(trusted=True), dry_run=True)


@pytest.fixture
def f_offline_use_case() -> GetPanelOverviewUseCase:
    zone = ZoneFactory.create(slug='hallway', name='Прихожая', thermostat__slug='thermostat-hallway')
    ComfortPeriodFactory.create_batch(7, zone=zone, start=time(6, 30), end=time(23, 30))
    store = ThermostatStateStore(unavailable_after=timedelta(minutes=10))
    store.record_failure('thermostat-hallway', 'timeout')
    return GetPanelOverviewUseCase(zones=ZoneRepository(), store=store, clock=ClockStub(trusted=False), dry_run=False)


def test_overview_combines_schedule_and_readings(f_use_case: GetPanelOverviewUseCase) -> None:
    output = f_use_case.execute(GetPanelOverviewInputDTO(now=MORNING))

    assert output == GetPanelOverviewOutputDTO(
        zones=(
            ZoneOverview(
                slug='kitchen',
                name='Кухня',
                kind=ZoneKind.AIR,
                mode=ScheduleMode.COMFORT,
                available=True,
                error=None,
                floor_temperature=Decimal('21.5'),
                power=True,
                setpoint=Decimal('22.0'),
                target_setpoint=Decimal('28.0'),
                boosted=False,
                live_control=False,
            ),
        ),
        clock=ClockStatus(trusted=True, reason='stub'),
        dry_run=True,
    )


def test_overview_shows_unread_thermostat_as_unavailable(f_offline_use_case: GetPanelOverviewUseCase) -> None:
    output = f_offline_use_case.execute(GetPanelOverviewInputDTO(now=MORNING))

    assert output == GetPanelOverviewOutputDTO(
        zones=(
            ZoneOverview(
                slug='hallway',
                name='Прихожая',
                kind=ZoneKind.AIR,
                mode=ScheduleMode.COMFORT,
                available=False,
                error='timeout',
                floor_temperature=None,
                power=None,
                setpoint=None,
                target_setpoint=Decimal('28.0'),
                boosted=False,
                live_control=False,
            ),
        ),
        clock=ClockStatus(trusted=False, reason='stub'),
        dry_run=False,
    )
