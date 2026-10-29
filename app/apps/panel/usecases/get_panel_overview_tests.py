from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.climate.domain.modes import ScheduleMode, ZoneKind
from apps.climate.domain.schedule import ScheduleTransition
from apps.climate.factories import ComfortPeriodFactory, ZoneFactory
from apps.climate.repositories.zone import ZoneRepository
from apps.devices.domain.readings import ThermostatMode, ThermostatState
from apps.devices.services.state_store import ThermostatStateStore
from apps.panel.usecases.get_panel_overview import (
    GetPanelOverviewInputDTO,
    GetPanelOverviewOutputDTO,
    GetPanelOverviewUseCase,
    ZoneOverview,
    ZoneRegime,
)
from apps.system.domain.clock import ClockStatus, ClockStatusCode
from apps.system.stubs import ClockStub

pytestmark = pytest.mark.django_db

MOSCOW = ZoneInfo('Europe/Moscow')
MORNING = datetime(2026, 10, 29, 7, 0, tzinfo=MOSCOW)


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
    store.record_failure('thermostat-hallway', error_code='device_unavailable', error='timeout')
    return GetPanelOverviewUseCase(zones=ZoneRepository(), store=store, clock=ClockStub(trusted=False), dry_run=False)


@pytest.fixture
def f_boosted_use_case() -> GetPanelOverviewUseCase:
    zone = ZoneFactory.create(
        slug='bathroom-2f',
        name='Санузел 2 этаж',
        thermostat__slug='thermostat-bathroom-2f',
        boost_until=MORNING + timedelta(hours=1),
    )
    ComfortPeriodFactory.create_batch(7, zone=zone, start=time(6, 30), end=time(23, 30))
    return GetPanelOverviewUseCase(
        zones=ZoneRepository(),
        store=ThermostatStateStore(unavailable_after=timedelta(minutes=10)),
        clock=ClockStub(trusted=True),
        dry_run=False,
    )


@pytest.fixture
def f_disabled_use_case() -> GetPanelOverviewUseCase:
    zone = ZoneFactory.create(
        slug='bathroom-1f',
        name='Санузел 1 этаж',
        thermostat__slug='thermostat-bathroom-1f',
        heating_enabled=False,
    )
    ComfortPeriodFactory.create_batch(7, zone=zone, start=time(6, 30), end=time(23, 30))
    return GetPanelOverviewUseCase(
        zones=ZoneRepository(),
        store=ThermostatStateStore(unavailable_after=timedelta(minutes=10)),
        clock=ClockStub(trusted=True),
        dry_run=False,
    )


@pytest.fixture
def f_two_zone_use_case() -> GetPanelOverviewUseCase:
    ZoneFactory.create(slug='kitchen', name='Кухня', thermostat__slug='thermostat-kitchen')
    ZoneFactory.create(slug='living-room', name='Гостиная', thermostat__slug='thermostat-living-room')
    return GetPanelOverviewUseCase(
        zones=ZoneRepository(),
        store=ThermostatStateStore(unavailable_after=timedelta(minutes=10)),
        clock=ClockStub(trusted=True),
        dry_run=True,
    )


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
                error_code=None,
                error=None,
                floor_temperature=Decimal('21.5'),
                power=True,
                setpoint=Decimal('22.0'),
                target_setpoint=Decimal('28.0'),
                boosted=False,
                live_control=False,
                regime=ZoneRegime.COMFORT,
                boost_until=None,
                next_change=ScheduleTransition(
                    mode=ScheduleMode.NIGHT,
                    at=datetime(2026, 10, 29, 23, 30, tzinfo=MOSCOW),
                ),
            ),
        ),
        clock=ClockStatus(trusted=True, code=ClockStatusCode.NTP_SYNCHRONIZED, reason='stub'),
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
                error_code='device_unavailable',
                error='timeout',
                floor_temperature=None,
                power=None,
                setpoint=None,
                target_setpoint=Decimal('28.0'),
                boosted=False,
                live_control=False,
                regime=ZoneRegime.COMFORT,
                boost_until=None,
                next_change=None,
            ),
        ),
        clock=ClockStatus(trusted=False, code=ClockStatusCode.NTP_NOT_SYNCHRONIZED, reason='stub'),
        dry_run=False,
    )


def test_overview_shows_boost_and_its_end(f_boosted_use_case: GetPanelOverviewUseCase) -> None:
    zone = f_boosted_use_case.execute(GetPanelOverviewInputDTO(now=MORNING)).zones[0]

    assert (zone.regime, zone.target_setpoint, zone.boosted, zone.boost_until) == (
        ZoneRegime.BOOST,
        Decimal('30.0'),
        True,
        MORNING + timedelta(hours=1),
    )


def test_overview_shows_disabled_zone_as_off_without_next_change(f_disabled_use_case: GetPanelOverviewUseCase) -> None:
    zone = f_disabled_use_case.execute(GetPanelOverviewInputDTO(now=MORNING)).zones[0]

    assert (zone.regime, zone.target_setpoint, zone.next_change) == (ZoneRegime.OFF, None, None)


def test_overview_lists_zones_by_name(f_two_zone_use_case: GetPanelOverviewUseCase) -> None:
    output = f_two_zone_use_case.execute(GetPanelOverviewInputDTO(now=MORNING))

    assert [zone.name for zone in output.zones] == ['Гостиная', 'Кухня']
