from datetime import time, timedelta
from decimal import Decimal

import pytest

from apps.climate.domain.climate_config import ClimateConfig, ClimateConfigRecord
from apps.climate.domain.comfort_windows import RoomWeek, StoredWindow
from apps.climate.domain.heat_sources import HeatSourceKind, SourceSettings
from apps.climate.domain.modes import ScheduleMode, ZoneKind
from apps.climate.domain.week_plan import DayPeriod
from apps.climate.domain.zone import AirTargets, FloorSetpoints, ZoneSettings
from apps.climate.stubs import ClimateConfigStoreStub, ComfortWindowStoreStub, HeatSourceStoreStub, ZoneStoreStub
from apps.house.domain.rooms import FloorRecord, RoomRecord
from apps.house.stubs import HouseStoreStub
from apps.panel.usecases.get_schedule_page import (
    GetSchedulePageInputDTO,
    GetSchedulePageUseCase,
    RoomSchedule,
    ScheduleDay,
    ZoneWarmUp,
)

MONDAY_WINDOW = StoredWindow(id=1, weekday=0, start=time(6, 30), end=time(23, 30))
TUESDAY_WINDOW = StoredWindow(id=2, weekday=1, start=time(7, 0), end=time(22, 0))
NIGHT_ALL_DAY = (DayPeriod(mode=ScheduleMode.NIGHT, start=0, end=1440),)
OFFICE_WINDOW = StoredWindow(id=3, weekday=0, start=time(13, 0), end=time(23, 45))
CONVECTOR = SourceSettings(
    slug='convector-office',
    name='Конвектор: кабинет',
    room_slug='office',
    room_name='Кабинет',
    kind=HeatSourceKind.HEATER,
    device_slug='heater-office',
    priority=1,
    live=False,
    setpoint_bias=Decimal('0.0'),
    max_setpoint=Decimal('24.0'),
    min_outdoor=None,
    write_interval=timedelta(minutes=5),
    rated_watts=2000,
    manual_until=None,
    last_command_at=None,
    last_dry_run_at=None,
    commanded_power=None,
    commanded_setpoint=None,
    load='convector-office',
    load_live=False,
    load_management_live=False,
)


def _zone(
    slug: str,
    name: str,
    kind: ZoneKind,
    *,
    room: str | None,
    air_sensor: str | None,
    air_live: bool,
    lead: int,
) -> ZoneSettings:
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
        room_slug=room,
    )


def _room(slug: str, name: str, floor: str, level: int, position: int) -> RoomRecord:
    return RoomRecord(slug=slug, name=name, floor_slug=floor, floor_level=level, position=position)


def _climate_settings(lead_cap_minutes: int) -> ClimateConfigStoreStub:
    return ClimateConfigStoreStub(
        record=ClimateConfigRecord(
            id=1,
            config=ClimateConfig(
                air_start_offset=Decimal('0.30'),
                initial_early_stop_offset=Decimal('0.30'),
                air_min_band=Decimal('0.20'),
                learned_mornings_needed=3,
                preheat_lead_cap_minutes=lead_cap_minutes,
                guests_end_hour=14,
            ),
        ),
    )


@pytest.fixture
def zones() -> ZoneStoreStub:
    return ZoneStoreStub(
        zones=[
            _zone(
                'kitchen',
                'Кухня',
                ZoneKind.AIR,
                room='open-space',
                air_sensor='air-kitchen',
                air_live=True,
                lead=180,
            ),
            _zone(
                'living-room',
                'Гостиная',
                ZoneKind.AIR,
                room='open-space',
                air_sensor='air-living-room',
                air_live=False,
                lead=150,
            ),
            _zone(
                'hallway',
                'Прихожая',
                ZoneKind.AIR,
                room='open-space',
                air_sensor=None,
                air_live=True,
                lead=240,
            ),
            _zone(
                'bathroom-1f',
                'Санузел 1 этаж',
                ZoneKind.FLOOR,
                room='bathroom-1f',
                air_sensor=None,
                air_live=False,
                lead=120,
            ),
            _zone(
                'bathroom-2f',
                'Санузел 2 этаж',
                ZoneKind.FLOOR,
                room=None,
                air_sensor=None,
                air_live=False,
                lead=180,
            ),
        ],
        errors={},
    )


@pytest.fixture
def windows() -> ComfortWindowStoreStub:
    return ComfortWindowStoreStub(
        weeks=[
            RoomWeek(slug='open-space', name='Гостиная, кухня и прихожая', windows=(MONDAY_WINDOW,)),
            RoomWeek(slug='bathroom-1f', name='Санузел 1 этаж', windows=(TUESDAY_WINDOW,)),
            RoomWeek(slug='bedroom', name='Спальня', windows=()),
        ],
    )


@pytest.fixture
def house() -> HouseStoreStub:
    return HouseStoreStub(
        floors=[
            FloorRecord(
                slug='floor-1',
                name='1 этаж',
                level=1,
                rooms=(
                    _room('open-space', 'Гостиная, кухня и прихожая', 'floor-1', 1, 1),
                    _room('bathroom-1f', 'Санузел 1 этаж', 'floor-1', 1, 2),
                ),
            ),
            FloorRecord(
                slug='floor-2',
                name='2 этаж',
                level=2,
                rooms=(_room('bedroom', 'Спальня', 'floor-2', 2, 1), _room('office', 'Кабинет', 'floor-2', 2, 2)),
            ),
        ],
    )


@pytest.fixture
def f_use_case(zones: ZoneStoreStub, windows: ComfortWindowStoreStub, house: HouseStoreStub) -> GetSchedulePageUseCase:
    return GetSchedulePageUseCase(
        zones=zones,
        windows=windows,
        house=house,
        climate_settings=_climate_settings(240),
        sources=HeatSourceStoreStub(sources=[], errors={}),
    )


@pytest.fixture
def f_capped_use_case(
    zones: ZoneStoreStub,
    windows: ComfortWindowStoreStub,
    house: HouseStoreStub,
) -> GetSchedulePageUseCase:
    return GetSchedulePageUseCase(
        zones=zones,
        windows=windows,
        house=house,
        climate_settings=_climate_settings(90),
        sources=HeatSourceStoreStub(sources=[], errors={}),
    )


@pytest.fixture
def f_office_use_case(zones: ZoneStoreStub, house: HouseStoreStub) -> GetSchedulePageUseCase:
    return GetSchedulePageUseCase(
        zones=zones,
        windows=ComfortWindowStoreStub(weeks=[RoomWeek(slug='office', name='Кабинет', windows=(OFFICE_WINDOW,))]),
        house=house,
        climate_settings=_climate_settings(240),
        sources=HeatSourceStoreStub(sources=[CONVECTOR], errors={}),
    )


def test_schedule_lists_rooms_by_floor_with_the_warm_up_of_each_of_their_zones(
    f_use_case: GetSchedulePageUseCase,
) -> None:
    output = f_use_case.execute(GetSchedulePageInputDTO())

    assert (
        [(room.slug, room.floor_name, room.preheat_lead, room.zones) for room in output.rooms],
        output.lead_cap,
        output.mornings_needed,
    ) == (
        [
            (
                'open-space',
                '1 этаж',
                timedelta(hours=4),
                (
                    ZoneWarmUp(
                        slug='living-room',
                        name='Гостиная',
                        preheat_lead=timedelta(minutes=150),
                        optimal_start=False,
                    ),
                    ZoneWarmUp(slug='kitchen', name='Кухня', preheat_lead=timedelta(hours=3), optimal_start=True),
                    ZoneWarmUp(slug='hallway', name='Прихожая', preheat_lead=timedelta(hours=4), optimal_start=False),
                ),
            ),
            (
                'bathroom-1f',
                '1 этаж',
                timedelta(hours=2),
                (
                    ZoneWarmUp(
                        slug='bathroom-1f',
                        name='Санузел 1 этаж',
                        preheat_lead=timedelta(hours=2),
                        optimal_start=False,
                    ),
                ),
            ),
            ('bedroom', '2 этаж', timedelta(0), ()),
        ],
        timedelta(hours=4),
        3,
    )


def test_schedule_caps_the_lead_only_of_a_zone_under_air_control(f_capped_use_case: GetSchedulePageUseCase) -> None:
    output = f_capped_use_case.execute(GetSchedulePageInputDTO())

    open_space = output.room('open-space')
    assert (
        [(zone.slug, zone.preheat_lead) for zone in open_space.zones] if open_space else None,
        open_space.preheat_lead if open_space else None,
        output.lead_cap,
    ) == (
        [('living-room', timedelta(minutes=150)), ('kitchen', timedelta(minutes=90)), ('hallway', timedelta(hours=4))],
        timedelta(hours=4),
        timedelta(minutes=90),
    )


def test_schedule_draws_the_days_of_a_room_by_the_longest_warm_up_of_its_zones(
    f_use_case: GetSchedulePageUseCase,
) -> None:
    output = f_use_case.execute(GetSchedulePageInputDTO())

    open_space, bathroom = output.room('open-space'), output.room('bathroom-1f')
    assert (
        open_space.days[0] if open_space else None,
        open_space.days[1:] if open_space else None,
        bathroom.days[1].periods if bathroom else None,
    ) == (
        ScheduleDay(
            weekday=0,
            windows=(MONDAY_WINDOW,),
            periods=(
                DayPeriod(mode=ScheduleMode.NIGHT, start=0, end=150),
                DayPeriod(mode=ScheduleMode.PREHEAT, start=150, end=390),
                DayPeriod(mode=ScheduleMode.COMFORT, start=390, end=1410),
                DayPeriod(mode=ScheduleMode.NIGHT, start=1410, end=1440),
            ),
        ),
        tuple(ScheduleDay(weekday=weekday, windows=(), periods=NIGHT_ALL_DAY) for weekday in range(1, 7)),
        (
            DayPeriod(mode=ScheduleMode.NIGHT, start=0, end=300),
            DayPeriod(mode=ScheduleMode.PREHEAT, start=300, end=420),
            DayPeriod(mode=ScheduleMode.COMFORT, start=420, end=1320),
            DayPeriod(mode=ScheduleMode.NIGHT, start=1320, end=1440),
        ),
    )


def test_schedule_finds_a_room_by_slug_or_by_one_of_its_windows(f_use_case: GetSchedulePageUseCase) -> None:
    output = f_use_case.execute(GetSchedulePageInputDTO())

    assert (
        [room.slug if room else None for room in (output.room('bathroom-1f'), output.room('office'))],
        [room.slug if room else None for room in (output.room_of_window(2), output.room_of_window(9))],
    ) == (['bathroom-1f', None], ['bathroom-1f', None])


def test_room_without_zones_is_night_every_day_and_warms_up_nothing(f_use_case: GetSchedulePageUseCase) -> None:
    output = f_use_case.execute(GetSchedulePageInputDTO())

    assert output.room('bedroom') == RoomSchedule(
        slug='bedroom',
        name='Спальня',
        floor_name='2 этаж',
        preheat_lead=timedelta(0),
        zones=(),
        sources=(),
        days=tuple(ScheduleDay(weekday=weekday, windows=(), periods=NIGHT_ALL_DAY) for weekday in range(7)),
    )


def test_room_heated_by_a_source_teplo_commands_names_it_and_warms_up_nothing(
    f_office_use_case: GetSchedulePageUseCase,
) -> None:
    output = f_office_use_case.execute(GetSchedulePageInputDTO())

    office = output.room('office')
    assert (
        office.sources if office else None,
        office.zones if office else None,
        office.days[0].periods if office else None,
    ) == (
        ('Конвектор: кабинет',),
        (),
        (
            DayPeriod(mode=ScheduleMode.NIGHT, start=0, end=780),
            DayPeriod(mode=ScheduleMode.COMFORT, start=780, end=1425),
            DayPeriod(mode=ScheduleMode.NIGHT, start=1425, end=1440),
        ),
    )
