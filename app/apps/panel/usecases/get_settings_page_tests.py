from decimal import Decimal

import pytest

from apps.climate.domain.climate_config import ClimateConfig, ClimateConfigRecord
from apps.climate.domain.modes import ZoneKind
from apps.climate.domain.room_climate import RoomClimateConfig, RoomClimateConfigRecord
from apps.climate.domain.zone_config import ZoneConfig, ZoneConfigRecord
from apps.climate.stubs import ClimateConfigStoreStub, RoomClimateConfigStoreStub, ZoneConfigStoreStub
from apps.panel.usecases.get_settings_page import (
    GetSettingsPageInputDTO,
    GetSettingsPageOutputDTO,
    GetSettingsPageUseCase,
)
from apps.power.domain.load_config import LoadConfig, LoadConfigRecord, LoadSettingsConfig, LoadSettingsRecord
from apps.power.domain.loads import LoadKind, LoadPhase
from apps.power.stubs import LoadConfigStoreStub, LoadSettingsConfigStoreStub

KITCHEN = ZoneConfig(
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
KITCHEN_RECORD = ZoneConfigRecord(
    id=2,
    slug='kitchen',
    kind=ZoneKind.AIR,
    config=KITCHEN,
    live_control=True,
    air_control_live=False,
    room='open-space',
)
BATHROOM_RECORD = ZoneConfigRecord(
    id=1,
    slug='bathroom-1f',
    kind=ZoneKind.FLOOR,
    config=ZoneConfig(
        name='Санузел 1 этаж',
        heating_enabled=False,
        comfort_floor_setpoint=Decimal('18.0'),
        night_floor_setpoint=Decimal('18.0'),
        boost_floor_setpoint=Decimal('30.0'),
        idle_floor_setpoint=Decimal('18.0'),
        floor_limit_setpoint=Decimal('30.0'),
        comfort_air_target=Decimal('22.0'),
        night_air_target=Decimal('19.5'),
        preheat_lead_minutes=180,
    ),
    live_control=True,
    air_control_live=False,
)
CLIMATE = ClimateConfigRecord(
    id=None,
    config=ClimateConfig(
        air_start_offset=Decimal('0.30'),
        initial_early_stop_offset=Decimal('0.30'),
        air_min_band=Decimal('0.20'),
        learned_mornings_needed=3,
        preheat_lead_cap_minutes=240,
    ),
)
OPEN_SPACE_RECORD = RoomClimateConfigRecord(
    id=1,
    slug='open-space',
    name='Гостиная, кухня и прихожая',
    floor_name='1 этаж',
    config=RoomClimateConfig(
        comfort_target=Decimal('22.0'),
        setback_target=Decimal('19.5'),
        backup_below=Decimal('19.0'),
        backup_hysteresis=Decimal('0.30'),
    ),
    air_sensor_slug=None,
    backup_zone_slug=None,
    backup_zone_name=None,
    backup_live=False,
)
BEDROOM_RECORD = RoomClimateConfigRecord(
    id=2,
    slug='bedroom',
    name='Спальня',
    floor_name='2 этаж',
    config=RoomClimateConfig(
        comfort_target=Decimal('20.5'),
        setback_target=Decimal('19.5'),
        backup_below=Decimal('19.0'),
        backup_hysteresis=Decimal('0.30'),
    ),
    air_sensor_slug='air-second-floor',
    backup_zone_slug='bathroom-2f',
    backup_zone_name='Санузел 2 этаж',
    backup_live=True,
)
LOAD_SETTINGS = LoadSettingsRecord(
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
)
BOILER_RECORD = LoadConfigRecord(
    id=1,
    slug='boiler-1f',
    name='Бойлер 1 этаж',
    kind=LoadKind.PLUG,
    config=LoadConfig(priority=1, phase=LoadPhase.UNKNOWN, nominal_amps=Decimal('6.0')),
    live=True,
)
KITCHEN_LOAD_RECORD = LoadConfigRecord(
    id=2,
    slug='kitchen',
    name='Кухня',
    kind=LoadKind.ZONE,
    config=LoadConfig(priority=5, phase=LoadPhase.L2, nominal_amps=None),
    live=False,
)


@pytest.fixture
def f_use_case() -> GetSettingsPageUseCase:
    return GetSettingsPageUseCase(
        rooms=RoomClimateConfigStoreStub(records=[OPEN_SPACE_RECORD, BEDROOM_RECORD]),
        zones=ZoneConfigStoreStub(records=[BATHROOM_RECORD, KITCHEN_RECORD]),
        climate_settings=ClimateConfigStoreStub(record=CLIMATE),
        load_settings=LoadSettingsConfigStoreStub(record=LOAD_SETTINGS),
        loads=LoadConfigStoreStub(records=[KITCHEN_LOAD_RECORD, BOILER_RECORD]),
        dry_run=True,
    )


def test_settings_page_lists_rooms_in_floor_order_zones_by_name_and_loads_by_priority_with_their_gates(
    f_use_case: GetSettingsPageUseCase,
) -> None:
    output = f_use_case.execute(GetSettingsPageInputDTO())

    assert (output, output.zone('kitchen'), output.zone('attic'), output.room('bedroom'), output.room('office')) == (
        GetSettingsPageOutputDTO(
            rooms=(OPEN_SPACE_RECORD, BEDROOM_RECORD),
            zones=(KITCHEN_RECORD, BATHROOM_RECORD),
            climate=CLIMATE,
            load_settings=LOAD_SETTINGS,
            loads=(BOILER_RECORD, KITCHEN_LOAD_RECORD),
            dry_run=True,
        ),
        KITCHEN_RECORD,
        None,
        BEDROOM_RECORD,
        None,
    )
