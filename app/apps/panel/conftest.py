from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.utils import translation

from apps.climate.domain.climate_config import ClimateConfig, ClimateConfigRecord
from apps.climate.domain.comfort_windows import StoredWindow
from apps.climate.domain.modes import ControlPath, ScheduleMode, ZoneKind
from apps.climate.domain.room_climate import RoomClimateConfig, RoomClimateConfigRecord
from apps.climate.domain.room_intent import IntentReason, RoomIntent
from apps.climate.domain.schedule import ScheduleTransition
from apps.climate.domain.week_plan import DayPeriod
from apps.climate.domain.zone_config import ZoneConfig, ZoneConfigRecord
from apps.climate.domain.zone_intent import ForcedTarget
from apps.devices.domain.kinds import DeviceKind
from apps.panel.usecases.get_panel_overview import (
    FloorOverview,
    GetPanelOverviewOutputDTO,
    LoadOverview,
    OccupancyOverview,
    PlugOverview,
    PresenceOverview,
    RoomComfortOverview,
    RoomOverview,
    SensorOverview,
    ZoneOverview,
    ZoneRegime,
)
from apps.panel.usecases.get_phase_currents import GetPhaseCurrentsOutputDTO, PhaseCurrent, PhaseMeterOverview
from apps.panel.usecases.get_schedule_page import GetSchedulePageOutputDTO, RoomSchedule, ScheduleDay, ZoneWarmUp
from apps.panel.usecases.get_settings_page import GetSettingsPageOutputDTO
from apps.power.domain.load_config import LoadConfig, LoadConfigRecord, LoadSettingsConfig, LoadSettingsRecord
from apps.power.domain.load_state import LoadState
from apps.power.domain.loads import AmpsOrigin, LoadKind, LoadPhase, PhaseOrigin
from apps.presence.domain.occupancy import Occupancy
from apps.system.domain.clock import ClockStatus, ClockStatusCode


@pytest.fixture
def panel_overview() -> GetPanelOverviewOutputDTO:
    air_kitchen = SensorOverview(
        slug='air-kitchen',
        name='Воздух: кухня',
        kind=DeviceKind.TEMPERATURE_SENSOR,
        available=True,
        error_code=None,
        error=None,
        temperature=Decimal('21.3'),
        humidity=Decimal('45.2'),
        age_seconds=12,
    )
    air_second_floor = SensorOverview(
        slug='air-second-floor',
        name='Воздух: 2 этаж',
        kind=DeviceKind.TEMPERATURE_SENSOR,
        available=False,
        error_code='sensor_reading_failed',
        error='temperature: the node reported nan',
        temperature=Decimal('19.8'),
        humidity=None,
        age_seconds=640,
    )
    outdoor = SensorOverview(
        slug='outdoor',
        name='Улица',
        kind=DeviceKind.TEMPERATURE_SENSOR,
        available=True,
        error_code=None,
        error=None,
        temperature=Decimal('-3.4'),
        humidity=None,
        age_seconds=20,
    )
    kitchen = ZoneOverview(
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
        air_control_live=False,
        path=ControlPath.AIR_DRY_RUN,
        air_target=Decimal('22.0'),
        regime=ZoneRegime.COMFORT,
        lead=None,
        boost_until=None,
        next_change=ScheduleTransition(
            mode=ScheduleMode.NIGHT,
            at=datetime(2026, 10, 29, 23, 30, tzinfo=ZoneInfo('Europe/Moscow')),
        ),
        air=air_kitchen,
    )
    plugs = (
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
            error_code='device_protocol_error',
            error='unexpected plug payload: {}',
            on=None,
            power_watts=None,
            current_amps=None,
            voltage_volts=None,
        ),
    )
    return GetPanelOverviewOutputDTO(
        zones=(kitchen,),
        plugs=plugs,
        loads=(
            LoadOverview(
                slug='boiler-1f',
                name='Бойлер 1 этаж',
                kind=LoadKind.PLUG,
                zone=None,
                device='plug-boiler-1f',
                priority=1,
                phase=LoadPhase.L2,
                phase_origin=PhaseOrigin.LEARNED,
                learned_phase=LoadPhase.L2,
                learned_amps=Decimal('6.6'),
                learning_samples=3,
                nominal_amps=Decimal('6.0'),
                amps=Decimal('6.0'),
                amps_origin=AmpsOrigin.NOMINAL,
                state=LoadState.SHED_DRY_RUN,
                live=False,
            ),
            LoadOverview(
                slug='kitchen',
                name='Кухня',
                kind=LoadKind.ZONE,
                zone='kitchen',
                device='thermostat-kitchen',
                priority=5,
                phase=LoadPhase.UNKNOWN,
                phase_origin=PhaseOrigin.UNKNOWN,
                learned_phase=None,
                learned_amps=None,
                learning_samples=0,
                nominal_amps=None,
                amps=Decimal('6.0'),
                amps_origin=AmpsOrigin.DEFAULT,
                state=LoadState.DRAWING,
                live=True,
            ),
        ),
        sensors=(air_second_floor, air_kitchen, outdoor),
        outdoor=outdoor,
        standalone_sensors=(air_second_floor,),
        floors=(),
        unplaced_zones=(kitchen,),
        unplaced_plugs=plugs,
        outages=(),
        clock=ClockStatus(trusted=True, code=ClockStatusCode.NTP_SYNCHRONIZED, reason='ntp synchronized'),
        dry_run=True,
    )


@pytest.fixture
def house_overview(panel_overview: GetPanelOverviewOutputDTO) -> GetPanelOverviewOutputDTO:
    air_second_floor, air_kitchen, outdoor = panel_overview.sensors
    kitchen_air = replace(air_kitchen, room='open-space')
    bedroom_air = replace(
        air_second_floor,
        name='Воздух: спальня',
        available=True,
        error_code=None,
        error=None,
        humidity=Decimal('38.0'),
        age_seconds=12,
        room='bedroom',
    )
    kitchen = replace(panel_overview.zones[0], air=kitchen_air, room='open-space')
    boiler = replace(panel_overview.plugs[0], room='open-space')
    return replace(
        panel_overview,
        zones=(kitchen,),
        plugs=(boiler,),
        sensors=(kitchen_air, bedroom_air, outdoor),
        standalone_sensors=(),
        floors=(
            FloorOverview(
                slug='floor-1',
                name='1 этаж',
                rooms=(
                    RoomOverview(
                        slug='open-space',
                        name='Гостиная, кухня и прихожая',
                        floor='floor-1',
                        zones=(kitchen,),
                        sensors=(),
                        plugs=(boiler,),
                        devices=('air-kitchen', 'plug-boiler-1f', 'thermostat-kitchen'),
                    ),
                ),
            ),
            FloorOverview(
                slug='floor-2',
                name='2 этаж',
                rooms=(
                    RoomOverview(
                        slug='bedroom',
                        name='Спальня',
                        floor='floor-2',
                        zones=(),
                        sensors=(bedroom_air,),
                        plugs=(),
                        devices=('air-second-floor',),
                    ),
                    RoomOverview(
                        slug='office',
                        name='Кабинет',
                        floor='floor-2',
                        zones=(),
                        sensors=(),
                        plugs=(),
                        devices=(),
                    ),
                ),
            ),
        ),
        unplaced_zones=(),
        unplaced_plugs=(),
    )


@pytest.fixture
def occupancy_overview(house_overview: GetPanelOverviewOutputDTO) -> GetPanelOverviewOutputDTO:
    computer = PresenceOverview(
        slug='pc-office',
        name='Компьютер: кабинет',
        room='office',
        node='teplo-pc-office',
        available=True,
        present=False,
        age_seconds=12,
        seen_age_seconds=300,
        error_code=None,
        error=None,
    )
    radar = PresenceOverview(
        slug='presence-office',
        name='Радар: кабинет',
        room='office',
        node='teplo-air-office',
        available=False,
        present=None,
        age_seconds=None,
        seen_age_seconds=None,
        error_code='broker_unavailable',
        error='mqtt broker unreachable',
    )
    ground, upstairs = house_overview.floors
    bedroom, office = upstairs.rooms
    watched_office = replace(
        office,
        devices=('pc-office', 'presence-office'),
        comfort=RoomComfortOverview(
            comfort_target=Decimal('21.5'),
            setback_target=Decimal('19.0'),
            air_sensor=None,
            backup_zone=None,
            backup_zone_name=None,
            backup_below=Decimal('19.0'),
            backup_live=False,
            backup_active=False,
        ),
        intent=RoomIntent(
            room='office',
            reason=IntentReason.OVERRIDE,
            target=Decimal('22.5'),
            until=datetime(2026, 11, 4, 18, 40, tzinfo=ZoneInfo('Europe/Moscow')),
            forced=(ForcedTarget(target=Decimal('22.5'), beneath=Decimal('19.0')),),
        ),
        adjustable=True,
        occupancy=OccupancyOverview(state=Occupancy.VACANT, follows=False, sources=(computer, radar)),
    )
    return replace(
        house_overview,
        floors=(ground, replace(upstairs, rooms=(bedroom, watched_office))),
        presence=(computer, radar),
    )


@pytest.fixture
def phase_currents() -> GetPhaseCurrentsOutputDTO:
    return GetPhaseCurrentsOutputDTO(
        meter=PhaseMeterOverview(
            stale=False,
            error_code=None,
            error=None,
            phases=(
                PhaseCurrent(phase='l1', amps=Decimal('12.40'), stale=False, age_seconds=1),
                PhaseCurrent(phase='l2', amps=Decimal('3.05'), stale=False, age_seconds=1),
                PhaseCurrent(phase='l3', amps=Decimal('27.81'), stale=False, age_seconds=2, ev_amps=Decimal('16.1')),
            ),
        ),
        scale_amps=Decimal(25),
        start_ceiling_amps=Decimal(20),
        shed_amps=Decimal(24),
    )


@pytest.fixture
def schedule_page() -> GetSchedulePageOutputDTO:
    monday = (
        DayPeriod(mode=ScheduleMode.NIGHT, start=0, end=210),
        DayPeriod(mode=ScheduleMode.PREHEAT, start=210, end=390),
        DayPeriod(mode=ScheduleMode.COMFORT, start=390, end=1410),
        DayPeriod(mode=ScheduleMode.NIGHT, start=1410, end=1440),
    )
    night = (DayPeriod(mode=ScheduleMode.NIGHT, start=0, end=1440),)
    return GetSchedulePageOutputDTO(
        rooms=(
            RoomSchedule(
                slug='kitchen',
                name='Кухня',
                floor_name='1 этаж',
                preheat_lead=timedelta(hours=3),
                zones=(ZoneWarmUp(slug='kitchen', name='Кухня', preheat_lead=timedelta(hours=3), optimal_start=False),),
                sources=(),
                days=(
                    ScheduleDay(
                        weekday=0,
                        windows=(StoredWindow(id=1, weekday=0, start=time(6, 30), end=time(23, 30)),),
                        periods=monday,
                    ),
                    *(ScheduleDay(weekday=weekday, windows=(), periods=night) for weekday in range(1, 7)),
                ),
            ),
            RoomSchedule(
                slug='living-room',
                name='Гостиная',
                floor_name='1 этаж',
                preheat_lead=timedelta(hours=3),
                zones=(
                    ZoneWarmUp(
                        slug='living-room',
                        name='Гостиная',
                        preheat_lead=timedelta(hours=3),
                        optimal_start=True,
                    ),
                ),
                sources=(),
                days=tuple(ScheduleDay(weekday=weekday, windows=(), periods=night) for weekday in range(7)),
            ),
        ),
        lead_cap=timedelta(hours=4),
        mornings_needed=3,
    )


@pytest.fixture
def settings_page() -> GetSettingsPageOutputDTO:
    kitchen = ZoneConfig(
        name='Кухня',
        heating_enabled=True,
        comfort_floor_setpoint=Decimal('28.0'),
        night_floor_setpoint=Decimal('22.0'),
        boost_floor_setpoint=Decimal('30.0'),
        idle_floor_setpoint=Decimal('18.0'),
        floor_limit_setpoint=Decimal('30.0'),
        comfort_air_target=Decimal('22.0'),
        night_air_target=Decimal('19.5'),
        preheat_lead_minutes=180,
    )
    return GetSettingsPageOutputDTO(
        rooms=(
            RoomClimateConfigRecord(
                id=5,
                slug='bathroom-1f',
                name='Санузел 1 этаж',
                floor_name='1 этаж',
                config=RoomClimateConfig(
                    comfort_target=Decimal('22.0'),
                    setback_target=Decimal('19.5'),
                    backup_below=Decimal('19.0'),
                    backup_hysteresis=Decimal('0.30'),
                    guest_room=False,
                ),
                air_sensor_slug=None,
                backup_zone_slug=None,
                backup_zone_name=None,
                backup_live=False,
            ),
            RoomClimateConfigRecord(
                id=6,
                slug='bedroom',
                name='Спальня',
                floor_name='2 этаж',
                config=RoomClimateConfig(
                    comfort_target=Decimal('20.5'),
                    setback_target=Decimal('19.5'),
                    backup_below=Decimal('19.0'),
                    backup_hysteresis=Decimal('0.30'),
                    guest_room=False,
                ),
                air_sensor_slug='air-second-floor',
                backup_zone_slug='bathroom-2f',
                backup_zone_name='Санузел 2 этаж',
                backup_live=False,
            ),
        ),
        zones=(
            ZoneConfigRecord(
                id=3,
                slug='kitchen',
                kind=ZoneKind.AIR,
                config=kitchen,
                live_control=True,
                air_control_live=False,
            ),
            ZoneConfigRecord(
                id=4,
                slug='bathroom-1f',
                kind=ZoneKind.FLOOR,
                config=ZoneConfig(
                    name='Санузел 1 этаж',
                    heating_enabled=True,
                    comfort_floor_setpoint=Decimal('18.0'),
                    night_floor_setpoint=Decimal('18.0'),
                    boost_floor_setpoint=Decimal('30.0'),
                    idle_floor_setpoint=Decimal('18.0'),
                    floor_limit_setpoint=Decimal('30.0'),
                    comfort_air_target=Decimal('22.0'),
                    night_air_target=Decimal('19.5'),
                    preheat_lead_minutes=120,
                ),
                live_control=False,
                air_control_live=False,
                room='bathroom-1f',
            ),
        ),
        climate=ClimateConfigRecord(
            id=1,
            config=ClimateConfig(
                air_start_offset=Decimal('0.30'),
                initial_early_stop_offset=Decimal('0.30'),
                air_min_band=Decimal('0.20'),
                learned_mornings_needed=3,
                preheat_lead_cap_minutes=240,
                guests_end_hour=14,
            ),
        ),
        load_settings=LoadSettingsRecord(
            id=1,
            config=LoadSettingsConfig(
                start_ceiling_amps=Decimal('20.0'),
                shed_amps=Decimal('24.0'),
                shed_hold_seconds=3,
                restore_amps=Decimal('18.0'),
                restore_hold_seconds=120,
                start_interval_seconds=30,
                meter_lost_after_seconds=60,
                default_load_amps=Decimal('6.0'),
            ),
            load_management_live=False,
        ),
        loads=(
            LoadConfigRecord(
                id=1,
                slug='boiler-1f',
                name='Бойлер 1 этаж',
                kind=LoadKind.PLUG,
                config=LoadConfig(priority=1, phase=LoadPhase.UNKNOWN, nominal_amps=Decimal('6.0')),
                live=True,
            ),
            LoadConfigRecord(
                id=2,
                slug='kitchen',
                name='Кухня',
                kind=LoadKind.ZONE,
                config=LoadConfig(priority=2, phase=LoadPhase.L2, nominal_amps=None),
                live=False,
            ),
        ),
        dry_run=True,
    )


@pytest.fixture
def f_russian_client(f_owner: User) -> Iterator[Client]:
    client = Client(headers={'accept-language': 'ru'})
    client.force_login(f_owner)
    with translation.override('ru'):
        yield client
