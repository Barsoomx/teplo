from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.climate.domain.modes import ScheduleMode, ZoneKind
from apps.climate.domain.schedule import ScheduleTransition
from apps.climate.factories import ComfortPeriodFactory, ZoneFactory
from apps.climate.repositories.zone import ZoneRepository
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.domain.readings import PlugState, ThermostatMode, ThermostatState
from apps.devices.factories import DeviceFactory
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.state_store import PlugStateStore, ThermostatStateStore
from apps.panel.usecases.get_panel_overview import (
    GetPanelOverviewInputDTO,
    GetPanelOverviewOutputDTO,
    GetPanelOverviewUseCase,
    PlugOverview,
    ZoneOverview,
    ZoneRegime,
)
from apps.power.domain.outage import OutageCause, OutageRecord
from apps.power.factories import OutageFactory
from apps.power.models import Outage
from apps.power.repositories.outage import OutageRepository
from apps.system.domain.clock import ClockStatus, ClockStatusCode
from apps.system.stubs import ClockStub

pytestmark = pytest.mark.django_db

MOSCOW = ZoneInfo('Europe/Moscow')
MORNING = datetime(2026, 10, 29, 7, 0, tzinfo=MOSCOW)
EVENING_CUT = datetime(2026, 10, 28, 20, 24, tzinfo=MOSCOW)
MARKED_CUT = EVENING_CUT - timedelta(days=1)
NIGHT_CUT = datetime(2026, 10, 29, 3, 10, tzinfo=MOSCOW)


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
    return GetPanelOverviewUseCase(
        zones=ZoneRepository(),
        devices=DeviceRepository(),
        outages=OutageRepository(),
        thermostat_store=store,
        plug_store=PlugStateStore(unavailable_after=timedelta(minutes=10)),
        clock=ClockStub(trusted=True),
        dry_run=True,
    )


@pytest.fixture
def f_offline_use_case() -> GetPanelOverviewUseCase:
    zone = ZoneFactory.create(slug='hallway', name='Прихожая', thermostat__slug='thermostat-hallway')
    ComfortPeriodFactory.create_batch(7, zone=zone, start=time(6, 30), end=time(23, 30))
    store = ThermostatStateStore(unavailable_after=timedelta(minutes=10))
    store.record_failure('thermostat-hallway', error_code='device_unavailable', error='timeout')
    return GetPanelOverviewUseCase(
        zones=ZoneRepository(),
        devices=DeviceRepository(),
        outages=OutageRepository(),
        thermostat_store=store,
        plug_store=PlugStateStore(unavailable_after=timedelta(minutes=10)),
        clock=ClockStub(trusted=False),
        dry_run=False,
    )


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
        devices=DeviceRepository(),
        outages=OutageRepository(),
        thermostat_store=ThermostatStateStore(unavailable_after=timedelta(minutes=10)),
        plug_store=PlugStateStore(unavailable_after=timedelta(minutes=10)),
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
        devices=DeviceRepository(),
        outages=OutageRepository(),
        thermostat_store=ThermostatStateStore(unavailable_after=timedelta(minutes=10)),
        plug_store=PlugStateStore(unavailable_after=timedelta(minutes=10)),
        clock=ClockStub(trusted=True),
        dry_run=False,
    )


@pytest.fixture
def f_two_zone_use_case() -> GetPanelOverviewUseCase:
    ZoneFactory.create(slug='kitchen', name='Кухня', thermostat__slug='thermostat-kitchen')
    ZoneFactory.create(slug='living-room', name='Гостиная', thermostat__slug='thermostat-living-room')
    return GetPanelOverviewUseCase(
        zones=ZoneRepository(),
        devices=DeviceRepository(),
        outages=OutageRepository(),
        thermostat_store=ThermostatStateStore(unavailable_after=timedelta(minutes=10)),
        plug_store=PlugStateStore(unavailable_after=timedelta(minutes=10)),
        clock=ClockStub(trusted=True),
        dry_run=True,
    )


@pytest.fixture
def f_plugs_use_case() -> GetPanelOverviewUseCase:
    ZoneFactory.create(slug='kitchen', name='Кухня', thermostat__slug='thermostat-kitchen')
    DeviceFactory.create(
        slug='plug-boiler-2f',
        name='Бойлер 2 этаж',
        kind=DeviceKind.PLUG,
        driver=DeviceDriver.TUYA_PLUG,
    )
    DeviceFactory.create(
        slug='plug-boiler-1f',
        name='Бойлер 1 этаж',
        kind=DeviceKind.PLUG,
        driver=DeviceDriver.TUYA_PLUG,
    )
    plug_store = PlugStateStore(unavailable_after=timedelta(minutes=10))
    plug_store.record_state(
        'plug-boiler-1f',
        PlugState(
            on=True,
            power_watts=Decimal('1305.4'),
            current_amps=Decimal('6.02'),
            voltage_volts=Decimal('216.8'),
            read_at=MORNING,
        ),
    )
    plug_store.record_failure('plug-boiler-2f', error_code='device_unavailable', error='timeout')
    return GetPanelOverviewUseCase(
        zones=ZoneRepository(),
        devices=DeviceRepository(),
        outages=OutageRepository(),
        thermostat_store=ThermostatStateStore(unavailable_after=timedelta(minutes=10)),
        plug_store=plug_store,
        clock=ClockStub(trusted=True),
        dry_run=True,
    )


@pytest.fixture
def f_outages() -> list[Outage]:
    return [
        OutageFactory.create(began_at=EVENING_CUT, ended_at=EVENING_CUT + timedelta(minutes=9)),
        OutageFactory.create(
            began_at=MARKED_CUT,
            ended_at=MARKED_CUT + timedelta(minutes=5),
            cause=OutageCause.GRID,
            cause_marked_at=MORNING,
        ),
        OutageFactory.create(began_at=NIGHT_CUT, ended_at=NIGHT_CUT + timedelta(minutes=2)),
    ]


@pytest.fixture
def f_outages_use_case() -> GetPanelOverviewUseCase:
    return GetPanelOverviewUseCase(
        zones=ZoneRepository(),
        devices=DeviceRepository(),
        outages=OutageRepository(),
        thermostat_store=ThermostatStateStore(unavailable_after=timedelta(minutes=10)),
        plug_store=PlugStateStore(unavailable_after=timedelta(minutes=10)),
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
        plugs=(),
        outages=(),
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
        plugs=(),
        outages=(),
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


def test_overview_lists_plugs_by_name_with_their_readings(f_plugs_use_case: GetPanelOverviewUseCase) -> None:
    output = f_plugs_use_case.execute(GetPanelOverviewInputDTO(now=MORNING))

    assert output.plugs == (
        PlugOverview(
            slug='plug-boiler-1f',
            name='Бойлер 1 этаж',
            available=True,
            error_code=None,
            error=None,
            on=True,
            power_watts=Decimal('1305.4'),
            current_amps=Decimal('6.02'),
            voltage_volts=Decimal('216.8'),
        ),
        PlugOverview(
            slug='plug-boiler-2f',
            name='Бойлер 2 этаж',
            available=False,
            error_code='device_unavailable',
            error='timeout',
            on=None,
            power_watts=None,
            current_amps=None,
            voltage_volts=None,
        ),
    )


def test_overview_asks_about_unmarked_outages_newest_first(
    f_outages_use_case: GetPanelOverviewUseCase,
    f_outages: list[Outage],
) -> None:
    output = f_outages_use_case.execute(GetPanelOverviewInputDTO(now=MORNING))

    assert output.outages == (
        OutageRecord(
            id=f_outages[2].pk,
            began_at=NIGHT_CUT,
            ended_at=NIGHT_CUT + timedelta(minutes=2),
            cause=OutageCause.UNMARKED,
            cause_marked_at=None,
        ),
        OutageRecord(
            id=f_outages[0].pk,
            began_at=EVENING_CUT,
            ended_at=EVENING_CUT + timedelta(minutes=9),
            cause=OutageCause.UNMARKED,
            cause_marked_at=None,
        ),
    )
