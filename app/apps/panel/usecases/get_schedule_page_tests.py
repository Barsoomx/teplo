from datetime import time, timedelta
from decimal import Decimal

import pytest

from apps.climate.domain.climate_config import ClimateConfig, ClimateConfigRecord
from apps.climate.domain.comfort_windows import StoredWindow, ZoneWeek
from apps.climate.domain.modes import ScheduleMode, ZoneKind
from apps.climate.domain.week_plan import DayPeriod
from apps.climate.domain.zone import AirTargets, FloorSetpoints, ZoneSettings
from apps.climate.stubs import ClimateConfigStoreStub, ComfortWindowStoreStub, ZoneStoreStub
from apps.panel.usecases.get_schedule_page import (
    GetSchedulePageInputDTO,
    GetSchedulePageUseCase,
    ScheduleDay,
    ZoneSchedule,
)

MONDAY_WINDOW = StoredWindow(id=1, weekday=0, start=time(6, 30), end=time(23, 30))
TUESDAY_WINDOW = StoredWindow(id=2, weekday=1, start=time(7, 0), end=time(22, 0))
NIGHT_ALL_DAY = (DayPeriod(mode=ScheduleMode.NIGHT, start=0, end=1440),)


def _zone(slug: str, name: str, kind: ZoneKind, *, air_sensor: str | None, air_live: bool, lead: int) -> ZoneSettings:
    return ZoneSettings(
        slug=slug,
        name=name,
        kind=kind,
        thermostat_slug=f'thermostat-{slug}',
        air_sensor_slug=air_sensor,
        heating_enabled=True,
        live_control=True,
        air_control_live=air_live,
        setpoints=FloorSetpoints(
            comfort=Decimal('28.0'),
            night=Decimal('22.0'),
            boost=Decimal('30.0'),
            idle=Decimal('18.0'),
            limit=Decimal('30.0'),
        ),
        air_targets=AirTargets(comfort=Decimal('22.0'), night=Decimal('19.5')),
        preheat_lead=timedelta(minutes=lead),
        windows=(),
        boost_until=None,
        last_command_at=None,
        last_dry_run_at=None,
        last_air_dry_run_at=None,
    )


def _climate_settings(lead_cap_minutes: int) -> ClimateConfigStoreStub:
    return ClimateConfigStoreStub(
        record=ClimateConfigRecord(
            id=1,
            config=ClimateConfig(
                air_start_offset=Decimal('0.30'),
                initial_early_stop_offset=Decimal('0.30'),
                learned_mornings_needed=3,
                preheat_lead_cap_minutes=lead_cap_minutes,
                second_floor_guard_threshold=Decimal('19.0'),
                second_floor_guard_hysteresis=Decimal('0.30'),
            ),
            second_floor_guard_live=False,
        ),
    )


@pytest.fixture
def zones() -> ZoneStoreStub:
    return ZoneStoreStub(
        zones=[
            _zone('kitchen', 'Кухня', ZoneKind.AIR, air_sensor='air-kitchen', air_live=True, lead=180),
            _zone('living-room', 'Гостиная', ZoneKind.AIR, air_sensor='air-living-room', air_live=False, lead=150),
            _zone('bathroom-1f', 'Санузел 1 этаж', ZoneKind.FLOOR, air_sensor=None, air_live=False, lead=120),
            _zone('hallway', 'Прихожая', ZoneKind.AIR, air_sensor=None, air_live=True, lead=180),
        ],
        errors={},
    )


@pytest.fixture
def windows() -> ComfortWindowStoreStub:
    return ComfortWindowStoreStub(
        weeks=[
            ZoneWeek(slug='bathroom-1f', name='Санузел 1 этаж', windows=()),
            ZoneWeek(slug='hallway', name='Прихожая', windows=(TUESDAY_WINDOW,)),
            ZoneWeek(slug='kitchen', name='Кухня', windows=(MONDAY_WINDOW,)),
            ZoneWeek(slug='living-room', name='Гостиная', windows=()),
            ZoneWeek(slug='attic', name='Чердак', windows=()),
        ],
    )


@pytest.fixture
def f_use_case(zones: ZoneStoreStub, windows: ComfortWindowStoreStub) -> GetSchedulePageUseCase:
    return GetSchedulePageUseCase(zones=zones, windows=windows, climate_settings=_climate_settings(240))


@pytest.fixture
def f_capped_use_case(zones: ZoneStoreStub, windows: ComfortWindowStoreStub) -> GetSchedulePageUseCase:
    return GetSchedulePageUseCase(zones=zones, windows=windows, climate_settings=_climate_settings(90))


def test_schedule_lists_zones_by_name_with_their_lead_and_learned_start(f_use_case: GetSchedulePageUseCase) -> None:
    output = f_use_case.execute(GetSchedulePageInputDTO())

    assert (
        [(zone.slug, zone.preheat_lead, zone.optimal_start) for zone in output.zones],
        output.lead_cap,
        output.mornings_needed,
    ) == (
        [
            ('living-room', timedelta(minutes=150), False),
            ('kitchen', timedelta(hours=3), True),
            ('hallway', timedelta(hours=3), False),
            ('bathroom-1f', timedelta(hours=2), False),
        ],
        timedelta(hours=4),
        3,
    )


def test_schedule_caps_the_fixed_lead_only_of_a_zone_under_air_control(
    f_capped_use_case: GetSchedulePageUseCase,
) -> None:
    output = f_capped_use_case.execute(GetSchedulePageInputDTO())

    kitchen, hallway = output.zone('kitchen'), output.zone('hallway')
    assert (
        [(zone.slug, zone.preheat_lead) for zone in output.zones],
        output.lead_cap,
        kitchen.days[0].periods if kitchen else None,
        hallway.days[1].periods if hallway else None,
    ) == (
        [
            ('living-room', timedelta(minutes=150)),
            ('kitchen', timedelta(minutes=90)),
            ('hallway', timedelta(hours=3)),
            ('bathroom-1f', timedelta(hours=2)),
        ],
        timedelta(minutes=90),
        (
            DayPeriod(mode=ScheduleMode.NIGHT, start=0, end=300),
            DayPeriod(mode=ScheduleMode.PREHEAT, start=300, end=390),
            DayPeriod(mode=ScheduleMode.COMFORT, start=390, end=1410),
            DayPeriod(mode=ScheduleMode.NIGHT, start=1410, end=1440),
        ),
        (
            DayPeriod(mode=ScheduleMode.NIGHT, start=0, end=240),
            DayPeriod(mode=ScheduleMode.PREHEAT, start=240, end=420),
            DayPeriod(mode=ScheduleMode.COMFORT, start=420, end=1320),
            DayPeriod(mode=ScheduleMode.NIGHT, start=1320, end=1440),
        ),
    )


def test_schedule_gives_each_day_its_windows_and_the_periods_they_make(f_use_case: GetSchedulePageUseCase) -> None:
    output = f_use_case.execute(GetSchedulePageInputDTO())

    kitchen = output.zone('kitchen')
    assert (kitchen.days[0] if kitchen else None, kitchen.days[1:] if kitchen else None) == (
        ScheduleDay(
            weekday=0,
            windows=(MONDAY_WINDOW,),
            periods=(
                DayPeriod(mode=ScheduleMode.NIGHT, start=0, end=210),
                DayPeriod(mode=ScheduleMode.PREHEAT, start=210, end=390),
                DayPeriod(mode=ScheduleMode.COMFORT, start=390, end=1410),
                DayPeriod(mode=ScheduleMode.NIGHT, start=1410, end=1440),
            ),
        ),
        tuple(ScheduleDay(weekday=weekday, windows=(), periods=NIGHT_ALL_DAY) for weekday in range(1, 7)),
    )


def test_schedule_finds_a_zone_by_slug_or_by_one_of_its_windows(f_use_case: GetSchedulePageUseCase) -> None:
    output = f_use_case.execute(GetSchedulePageInputDTO())

    assert (
        [zone.slug if zone else None for zone in (output.zone('hallway'), output.zone('attic'))],
        [zone.slug if zone else None for zone in (output.zone_of_window(1), output.zone_of_window(9))],
    ) == (['hallway', None], ['kitchen', None])


def test_zone_schedule_without_windows_is_night_every_day(f_use_case: GetSchedulePageUseCase) -> None:
    output = f_use_case.execute(GetSchedulePageInputDTO())

    assert output.zone('bathroom-1f') == ZoneSchedule(
        slug='bathroom-1f',
        name='Санузел 1 этаж',
        preheat_lead=timedelta(hours=2),
        optimal_start=False,
        days=tuple(ScheduleDay(weekday=weekday, windows=(), periods=NIGHT_ALL_DAY) for weekday in range(7)),
    )
