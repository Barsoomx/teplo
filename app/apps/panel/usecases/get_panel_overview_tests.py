from dataclasses import replace
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.climate.domain.drive import PendingKind, PendingWrite
from apps.climate.domain.external_heat import FireWatch
from apps.climate.domain.heat_sources import HeatSourceKind
from apps.climate.domain.memory import ZoneMemory
from apps.climate.domain.modes import AirState, ControlPath, ScheduleMode, ZoneKind
from apps.climate.domain.optimal_start import PreheatStart
from apps.climate.domain.room_intent import IntentReason, RoomIntent
from apps.climate.domain.schedule import ScheduleTransition
from apps.climate.domain.zone_intent import ForcedTarget
from apps.climate.factories import (
    ClimateSettingsFactory,
    ComfortPeriodFactory,
    HeatSourceFactory,
    RoomClimateFactory,
    ZoneFactory,
    ZoneLearningFactory,
)
from apps.climate.models import Zone
from apps.climate.repositories.climate_settings import ClimateSettingsRepository
from apps.climate.repositories.heat_source import HeatSourceRepository
from apps.climate.repositories.room_climate import RoomClimateRepository
from apps.climate.repositories.zone import ZoneRepository
from apps.climate.repositories.zone_learning import ZoneLearningRepository
from apps.climate.services.control_book import ControlBook
from apps.climate.services.room_intents import RoomIntentReader
from apps.core.stubs import MonotonicClockStub
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.domain.readings import PlugState, TemperatureReading, ThermostatMode, ThermostatState
from apps.devices.factories import DeviceFactory
from apps.devices.models import Device
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.state_store import PlugStateStore, TemperatureStateStore, ThermostatStateStore
from apps.house.factories import FloorFactory, RoomFactory
from apps.house.repositories.house import HouseRepository
from apps.panel.usecases.get_panel_overview import (
    GetPanelOverviewInputDTO,
    GetPanelOverviewOutputDTO,
    GetPanelOverviewUseCase,
    LoadOverview,
    PlugOverview,
    RoomComfortOverview,
    SensorOverview,
    SourceOverview,
    ZoneOverview,
    ZoneRegime,
)
from apps.power.domain.guard import GuardLoads, GuardState, ShedState
from apps.power.domain.load_state import LoadState
from apps.power.domain.loads import AmpsOrigin, LoadKind, LoadPhase, PhaseOrigin, ShedReason
from apps.power.domain.outage import OutageCause, OutageRecord, PrecedingOverload
from apps.power.domain.shedding import Shed
from apps.power.factories import LoadSettingsFactory, OutageFactory, PlugLoadFactory, ZoneLoadFactory
from apps.power.models import Outage
from apps.power.repositories.load import LoadRepository
from apps.power.repositories.load_settings import LoadSettingsRepository
from apps.power.repositories.outage import OutageRepository
from apps.power.services.guard_book import GuardBook
from apps.power.services.outage_overloads import OutageOverloads
from apps.system.domain.clock import ClockStatus, ClockStatusCode
from apps.system.stubs import ClockStub
from apps.telemetry.factories import JournalEntryFactory
from apps.telemetry.repositories.journal import JournalRepository

pytestmark = pytest.mark.django_db

MOSCOW = ZoneInfo('Europe/Moscow')
MORNING = datetime(2026, 10, 29, 7, 0, tzinfo=MOSCOW)
NIGHT = datetime(2026, 10, 29, 1, 0, tzinfo=MOSCOW)
WARM_UP = datetime(2026, 10, 29, 4, 0, tzinfo=MOSCOW)
LATE_WARM_UP = datetime(2026, 10, 29, 4, 30, tzinfo=MOSCOW)
COMFORT_AT = datetime(2026, 10, 29, 6, 30, tzinfo=MOSCOW)
LEARNED_LEAD = timedelta(hours=2, minutes=40)
EVENING_CUT = datetime(2026, 10, 28, 20, 24, tzinfo=MOSCOW)
MARKED_CUT = EVENING_CUT - timedelta(days=1)
NIGHT_CUT = datetime(2026, 10, 29, 3, 10, tzinfo=MOSCOW)
GUARD_AT = 1000.0
GUESTS_LEAVE = datetime(2026, 10, 30, 14, 0, tzinfo=MOSCOW)
BOILER_SHED = Shed(
    load='boiler-1f',
    phase=LoadPhase.L2,
    amps=Decimal('6.0'),
    live=False,
    reason=ShedReason.OVERLOAD,
    was_on=True,
    device='plug-boiler-1f',
    at=GUARD_AT,
    occurred_at=NIGHT_CUT,
    settled_at=GUARD_AT + 5,
)


def _sensor(slug: str, **fields: object) -> Device:
    return DeviceFactory.create(
        slug=slug,
        kind=DeviceKind.TEMPERATURE_SENSOR,
        driver=DeviceDriver.ESPHOME_TEMPERATURE,
        **fields,
    )


def _plug(slug: str, name: str, **fields: object) -> Device:
    return DeviceFactory.create(slug=slug, name=name, kind=DeviceKind.PLUG, driver=DeviceDriver.TUYA_PLUG, **fields)


def _zone(slug: str, name: str, **fields: object) -> Zone:
    zone = ZoneFactory.create(slug=slug, name=name, thermostat__slug=f'thermostat-{slug}', **fields)
    ComfortPeriodFactory.create_batch(7, zone=zone, start=time(6, 30), end=time(23, 30))
    return zone


def _read(temperatures: TemperatureStateStore, readings: dict[str, str]) -> None:
    for slug, temperature in readings.items():
        temperatures.record_state(
            slug,
            TemperatureReading(temperature=Decimal(temperature), humidity=None, read_at=MORNING),
        )


def _learned_house(air: str) -> TemperatureStateStore:
    zone = _zone('living-room', 'Гостиная', air_sensor=_sensor('air-living-room'), air_control_live=True)
    ZoneLearningFactory.create(zone=zone, dead_time=timedelta(minutes=40), heat_up_rates={'-5': '0.800'}, mornings=3)
    ClimateSettingsFactory.create(outdoor_sensor=_sensor('outdoor'))
    temperatures = TemperatureStateStore(unavailable_after=timedelta(minutes=10))
    _read(temperatures, {'air-living-room': air, 'outdoor': '-3.4'})
    return temperatures


def _intents(book: ControlBook) -> RoomIntentReader:
    return RoomIntentReader(
        rooms=RoomClimateRepository(),
        guests=ClimateSettingsRepository(),
        fireplaces=HeatSourceRepository(),
        book=book,
    )


def _use_case(temperatures: TemperatureStateStore, book: ControlBook) -> GetPanelOverviewUseCase:
    return GetPanelOverviewUseCase(
        zones=ZoneRepository(),
        intents=_intents(book),
        devices=DeviceRepository(),
        house=HouseRepository(),
        climate_settings=ClimateSettingsRepository(),
        learning=ZoneLearningRepository(),
        outages=OutageRepository(),
        overloads=OutageOverloads(journal=JournalRepository()),
        loads=LoadRepository(),
        load_settings=LoadSettingsRepository(),
        thermostat_store=ThermostatStateStore(unavailable_after=timedelta(minutes=10)),
        plug_store=PlugStateStore(unavailable_after=timedelta(minutes=10)),
        temperature_store=temperatures,
        book=book,
        guard_book=GuardBook(),
        clock=ClockStub(trusted=True),
        dry_run=False,
    )


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
            heating=True,
            read_at=MORNING,
        ),
    )
    return GetPanelOverviewUseCase(
        zones=ZoneRepository(),
        intents=_intents(ControlBook()),
        devices=DeviceRepository(),
        house=HouseRepository(),
        climate_settings=ClimateSettingsRepository(),
        learning=ZoneLearningRepository(),
        outages=OutageRepository(),
        overloads=OutageOverloads(journal=JournalRepository()),
        loads=LoadRepository(),
        load_settings=LoadSettingsRepository(),
        thermostat_store=store,
        plug_store=PlugStateStore(unavailable_after=timedelta(minutes=10)),
        temperature_store=TemperatureStateStore(unavailable_after=timedelta(minutes=10)),
        book=ControlBook(),
        guard_book=GuardBook(),
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
        intents=_intents(ControlBook()),
        devices=DeviceRepository(),
        house=HouseRepository(),
        climate_settings=ClimateSettingsRepository(),
        learning=ZoneLearningRepository(),
        outages=OutageRepository(),
        overloads=OutageOverloads(journal=JournalRepository()),
        loads=LoadRepository(),
        load_settings=LoadSettingsRepository(),
        thermostat_store=store,
        plug_store=PlugStateStore(unavailable_after=timedelta(minutes=10)),
        temperature_store=TemperatureStateStore(unavailable_after=timedelta(minutes=10)),
        book=ControlBook(),
        guard_book=GuardBook(),
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
        intents=_intents(ControlBook()),
        devices=DeviceRepository(),
        house=HouseRepository(),
        climate_settings=ClimateSettingsRepository(),
        learning=ZoneLearningRepository(),
        outages=OutageRepository(),
        overloads=OutageOverloads(journal=JournalRepository()),
        loads=LoadRepository(),
        load_settings=LoadSettingsRepository(),
        thermostat_store=ThermostatStateStore(unavailable_after=timedelta(minutes=10)),
        plug_store=PlugStateStore(unavailable_after=timedelta(minutes=10)),
        temperature_store=TemperatureStateStore(unavailable_after=timedelta(minutes=10)),
        book=ControlBook(),
        guard_book=GuardBook(),
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
        intents=_intents(ControlBook()),
        devices=DeviceRepository(),
        house=HouseRepository(),
        climate_settings=ClimateSettingsRepository(),
        learning=ZoneLearningRepository(),
        outages=OutageRepository(),
        overloads=OutageOverloads(journal=JournalRepository()),
        loads=LoadRepository(),
        load_settings=LoadSettingsRepository(),
        thermostat_store=ThermostatStateStore(unavailable_after=timedelta(minutes=10)),
        plug_store=PlugStateStore(unavailable_after=timedelta(minutes=10)),
        temperature_store=TemperatureStateStore(unavailable_after=timedelta(minutes=10)),
        book=ControlBook(),
        guard_book=GuardBook(),
        clock=ClockStub(trusted=True),
        dry_run=False,
    )


@pytest.fixture
def f_two_zone_use_case() -> GetPanelOverviewUseCase:
    ZoneFactory.create(slug='kitchen', name='Кухня', thermostat__slug='thermostat-kitchen')
    ZoneFactory.create(slug='living-room', name='Гостиная', thermostat__slug='thermostat-living-room')
    return GetPanelOverviewUseCase(
        zones=ZoneRepository(),
        intents=_intents(ControlBook()),
        devices=DeviceRepository(),
        house=HouseRepository(),
        climate_settings=ClimateSettingsRepository(),
        learning=ZoneLearningRepository(),
        outages=OutageRepository(),
        overloads=OutageOverloads(journal=JournalRepository()),
        loads=LoadRepository(),
        load_settings=LoadSettingsRepository(),
        thermostat_store=ThermostatStateStore(unavailable_after=timedelta(minutes=10)),
        plug_store=PlugStateStore(unavailable_after=timedelta(minutes=10)),
        temperature_store=TemperatureStateStore(unavailable_after=timedelta(minutes=10)),
        book=ControlBook(),
        guard_book=GuardBook(),
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
        intents=_intents(ControlBook()),
        devices=DeviceRepository(),
        house=HouseRepository(),
        climate_settings=ClimateSettingsRepository(),
        learning=ZoneLearningRepository(),
        outages=OutageRepository(),
        overloads=OutageOverloads(journal=JournalRepository()),
        loads=LoadRepository(),
        load_settings=LoadSettingsRepository(),
        thermostat_store=ThermostatStateStore(unavailable_after=timedelta(minutes=10)),
        plug_store=plug_store,
        temperature_store=TemperatureStateStore(unavailable_after=timedelta(minutes=10)),
        book=ControlBook(),
        guard_book=GuardBook(),
        clock=ClockStub(trusted=True),
        dry_run=True,
    )


@pytest.fixture
def monotonic() -> MonotonicClockStub:
    return MonotonicClockStub(now=1000.0)


@pytest.fixture
def f_sensors_use_case(monotonic: MonotonicClockStub) -> GetPanelOverviewUseCase:
    sensors = {
        slug: DeviceFactory.create(
            slug=slug,
            name=name,
            kind=DeviceKind.TEMPERATURE_SENSOR,
            driver=DeviceDriver.ESPHOME_TEMPERATURE,
        )
        for slug, name in (
            ('air-kitchen', 'Воздух: кухня'),
            ('air-hallway', 'Воздух: прихожая'),
            ('air-second-floor', 'Воздух: 2 этаж'),
            ('outdoor', 'Улица'),
        )
    }
    ZoneFactory.create(
        slug='kitchen',
        name='Кухня',
        thermostat__slug='thermostat-kitchen',
        air_sensor=sensors['air-kitchen'],
    )
    ZoneFactory.create(
        slug='hallway',
        name='Прихожая',
        thermostat__slug='thermostat-hallway',
        air_sensor=sensors['air-hallway'],
    )
    ZoneFactory.create(
        slug='bathroom-1f',
        name='Санузел 1 этаж',
        kind=ZoneKind.FLOOR,
        thermostat__slug='thermostat-bathroom-1f',
    )
    ClimateSettingsFactory.create(outdoor_sensor=sensors['outdoor'])
    store = TemperatureStateStore(unavailable_after=timedelta(minutes=10), clock=monotonic)
    store.record_state(
        'air-kitchen',
        TemperatureReading(temperature=Decimal('21.3'), humidity=Decimal('45.2'), read_at=MORNING),
    )
    store.record_state(
        'air-second-floor',
        TemperatureReading(temperature=Decimal('19.8'), humidity=Decimal('38.0'), read_at=MORNING),
    )
    store.record_state('outdoor', TemperatureReading(temperature=Decimal('-3.4'), humidity=None, read_at=MORNING))
    store.record_failure('air-hallway', error_code='broker_unavailable', error='connection refused')
    monotonic.advance(12.5)
    return GetPanelOverviewUseCase(
        zones=ZoneRepository(),
        intents=_intents(ControlBook()),
        devices=DeviceRepository(),
        house=HouseRepository(),
        climate_settings=ClimateSettingsRepository(),
        learning=ZoneLearningRepository(),
        outages=OutageRepository(),
        overloads=OutageOverloads(journal=JournalRepository()),
        loads=LoadRepository(),
        load_settings=LoadSettingsRepository(),
        thermostat_store=ThermostatStateStore(unavailable_after=timedelta(minutes=10)),
        plug_store=PlugStateStore(unavailable_after=timedelta(minutes=10)),
        temperature_store=store,
        book=ControlBook(),
        guard_book=GuardBook(),
        clock=ClockStub(trusted=True),
        dry_run=True,
    )


@pytest.fixture
def f_unplaced_sensor_use_case() -> GetPanelOverviewUseCase:
    DeviceFactory.create(
        slug='outdoor',
        name='Улица',
        kind=DeviceKind.TEMPERATURE_SENSOR,
        driver=DeviceDriver.ESPHOME_TEMPERATURE,
    )
    return GetPanelOverviewUseCase(
        zones=ZoneRepository(),
        intents=_intents(ControlBook()),
        devices=DeviceRepository(),
        house=HouseRepository(),
        climate_settings=ClimateSettingsRepository(),
        learning=ZoneLearningRepository(),
        outages=OutageRepository(),
        overloads=OutageOverloads(journal=JournalRepository()),
        loads=LoadRepository(),
        load_settings=LoadSettingsRepository(),
        thermostat_store=ThermostatStateStore(unavailable_after=timedelta(minutes=10)),
        plug_store=PlugStateStore(unavailable_after=timedelta(minutes=10)),
        temperature_store=TemperatureStateStore(unavailable_after=timedelta(minutes=10)),
        book=ControlBook(),
        guard_book=GuardBook(),
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
def f_paths_use_case() -> GetPanelOverviewUseCase:
    _zone('kitchen', 'Кухня', air_sensor=_sensor('air-kitchen'))
    _zone('living-room', 'Гостиная', air_sensor=_sensor('air-living-room'), air_control_live=True)
    _zone('hallway', 'Прихожая', air_sensor=_sensor('air-hallway'), air_control_live=True)
    _zone('bathroom-2f', 'Санузел 2 этаж', kind=ZoneKind.FLOOR)
    temperatures = TemperatureStateStore(unavailable_after=timedelta(minutes=10))
    _read(temperatures, {'air-kitchen': '21.3', 'air-living-room': '21.0'})
    temperatures.record_failure('air-hallway', error_code='broker_unavailable', error='connection refused')
    return _use_case(temperatures, ControlBook())


@pytest.fixture
def f_learned_use_case() -> GetPanelOverviewUseCase:
    return _use_case(_learned_house('20.4'), ControlBook())


@pytest.fixture
def f_held_preheat_use_case() -> GetPanelOverviewUseCase:
    book = ControlBook()
    book.remember_zone(
        'living-room',
        ZoneMemory(
            path=ControlPath.AIR,
            mode=ScheduleMode.PREHEAT,
            air_state=AirState.HEAT,
            heat_target=Decimal('22.0'),
            preheat=PreheatStart(comfort_at=COMFORT_AT, lead=LEARNED_LEAD),
        ),
    )
    return _use_case(_learned_house('21.6'), book)


@pytest.fixture
def f_backup_use_case(request: pytest.FixtureRequest) -> GetPanelOverviewUseCase:
    zone = _zone('bathroom-2f', 'Санузел 2 этаж', kind=ZoneKind.FLOOR)
    RoomClimateFactory.create(room=RoomFactory.create(slug='bedroom'), backup_zone=zone, backup_live=request.param)
    book = ControlBook()
    book.remember_backup('bedroom', True)
    return _use_case(TemperatureStateStore(unavailable_after=timedelta(minutes=10)), book)


@pytest.fixture
def f_room_comfort_use_case() -> GetPanelOverviewUseCase:
    ground = FloorFactory.create(slug='floor-1', name='1 этаж', level=1)
    upstairs = FloorFactory.create(slug='floor-2', name='2 этаж', level=2)
    open_space = RoomFactory.create(slug='open-space', floor=ground)
    bedroom = RoomFactory.create(slug='bedroom', floor=upstairs, position=1)
    RoomFactory.create(slug='office', floor=upstairs, position=2)
    RoomClimateFactory.create(room=open_space, setback_target=Decimal('19.0'))
    RoomClimateFactory.create(
        room=bedroom,
        air_sensor=_sensor('air-second-floor', room=bedroom),
        comfort_target=Decimal('20.5'),
        backup_zone=_zone('bathroom-2f', 'Санузел 2 этаж', kind=ZoneKind.FLOOR),
    )
    book = ControlBook()
    book.remember_backup('bedroom', True)
    return _use_case(TemperatureStateStore(unavailable_after=timedelta(minutes=10)), book)


@pytest.fixture
def f_outages_use_case() -> GetPanelOverviewUseCase:
    return GetPanelOverviewUseCase(
        zones=ZoneRepository(),
        intents=_intents(ControlBook()),
        devices=DeviceRepository(),
        house=HouseRepository(),
        climate_settings=ClimateSettingsRepository(),
        learning=ZoneLearningRepository(),
        outages=OutageRepository(),
        overloads=OutageOverloads(journal=JournalRepository()),
        loads=LoadRepository(),
        load_settings=LoadSettingsRepository(),
        thermostat_store=ThermostatStateStore(unavailable_after=timedelta(minutes=10)),
        plug_store=PlugStateStore(unavailable_after=timedelta(minutes=10)),
        temperature_store=TemperatureStateStore(unavailable_after=timedelta(minutes=10)),
        book=ControlBook(),
        guard_book=GuardBook(),
        clock=ClockStub(trusted=True),
        dry_run=True,
    )


@pytest.fixture
def overload_before_the_night_cut() -> None:
    JournalEntryFactory.create(
        kind='phase_overload',
        subject='l2',
        payload={'phase': 'l2', 'amps': '26.4'},
        created_at=NIGHT_CUT - timedelta(minutes=1),
    )


@pytest.fixture
def f_loads_use_case() -> GetPanelOverviewUseCase:
    kitchen = ZoneFactory.create(slug='kitchen', name='Кухня', thermostat__slug='thermostat-kitchen', live_control=True)
    hallway = ZoneFactory.create(slug='hallway', name='Прихожая', thermostat__slug='thermostat-hallway')
    living_room = ZoneFactory.create(slug='living-room', name='Гостиная', thermostat__slug='thermostat-living-room')
    PlugLoadFactory.create(
        slug='boiler-1f',
        name='Бойлер 1 этаж',
        device__slug='plug-boiler-1f',
        device__name='Бойлер 1 этаж',
        priority=1,
        phase=LoadPhase.L2,
        nominal_amps=Decimal('6.0'),
    )
    PlugLoadFactory.create(
        slug='boiler-2f',
        name='Бойлер 2 этаж',
        device__slug='plug-boiler-2f',
        device__name='Бойлер 2 этаж',
        priority=2,
        learned_phase=LoadPhase.L3,
        learned_amps=Decimal('6.4'),
        learning_samples=3,
        live=True,
    )
    ZoneLoadFactory.create(slug='hallway', name='Прихожая', zone=hallway, priority=4)
    ZoneLoadFactory.create(slug='kitchen', name='Кухня', zone=kitchen, priority=5, live=True)
    ZoneLoadFactory.create(
        slug='living-room',
        name='Гостиная',
        zone=living_room,
        priority=7,
        phase=LoadPhase.L1,
        nominal_amps=Decimal('8.0'),
    )
    LoadSettingsFactory.create(load_management_live=True)
    thermostats = ThermostatStateStore(unavailable_after=timedelta(minutes=10))
    thermostats.record_state(
        'thermostat-kitchen',
        ThermostatState(
            power=True,
            mode=ThermostatMode.HOLD,
            setpoint=Decimal('28.0'),
            floor_temperature=Decimal('24.5'),
            heating=True,
            read_at=MORNING,
        ),
    )
    thermostats.record_state(
        'thermostat-living-room',
        ThermostatState(
            power=True,
            mode=ThermostatMode.HOLD,
            setpoint=Decimal('28.0'),
            floor_temperature=Decimal('26.1'),
            heating=None,
            read_at=MORNING,
        ),
    )
    thermostats.record_failure('thermostat-hallway', error_code='device_unavailable', error='timeout')
    plugs = PlugStateStore(unavailable_after=timedelta(minutes=10))
    for slug, on, current in (('plug-boiler-1f', True, '6.02'), ('plug-boiler-2f', False, '0.0')):
        plugs.record_state(
            slug,
            PlugState(
                on=on,
                power_watts=Decimal('1305.4') if on else Decimal('0.0'),
                current_amps=Decimal(current),
                voltage_volts=Decimal('216.8'),
                read_at=MORNING,
            ),
        )
    guard_loads = GuardLoads(
        records=tuple(LoadRepository.all_records()),
        control=LoadSettingsRepository.control(),
        live_zones=frozenset({'kitchen'}),
        meter='phase-meter',
        read_at=GUARD_AT,
    )
    guard_book = GuardBook()
    guard_book.remember(
        replace(GuardState.started(guard_loads, GUARD_AT), shedding=ShedState(sheds={'boiler-1f': BOILER_SHED})),
    )
    guard_book.note_waiting(frozenset({'kitchen'}))
    return GetPanelOverviewUseCase(
        zones=ZoneRepository(),
        intents=_intents(ControlBook()),
        devices=DeviceRepository(),
        house=HouseRepository(),
        climate_settings=ClimateSettingsRepository(),
        learning=ZoneLearningRepository(),
        outages=OutageRepository(),
        overloads=OutageOverloads(journal=JournalRepository()),
        loads=LoadRepository(),
        load_settings=LoadSettingsRepository(),
        thermostat_store=thermostats,
        plug_store=plugs,
        temperature_store=TemperatureStateStore(unavailable_after=timedelta(minutes=10)),
        book=ControlBook(),
        guard_book=guard_book,
        clock=ClockStub(trusted=True),
        dry_run=False,
    )


@pytest.fixture
def f_house_use_case() -> GetPanelOverviewUseCase:
    ground = FloorFactory.create(slug='floor-1', name='1 этаж', level=1)
    upstairs = FloorFactory.create(slug='floor-2', name='2 этаж', level=2)
    open_space = RoomFactory.create(slug='open-space', name='Гостиная, кухня и прихожая', floor=ground, position=1)
    bathroom = RoomFactory.create(slug='bathroom-1f', name='Санузел 1 этаж', floor=ground, position=2)
    bedroom = RoomFactory.create(slug='bedroom', name='Спальня', floor=upstairs, position=1)
    RoomFactory.create(slug='office', name='Кабинет', floor=upstairs, position=2)
    ZoneFactory.create(
        slug='kitchen',
        name='Кухня',
        thermostat__slug='thermostat-kitchen',
        thermostat__room=open_space,
        air_sensor=_sensor('air-kitchen', name='Воздух: кухня', room=open_space),
        room=open_space,
    )
    ZoneFactory.create(
        slug='hallway',
        name='Прихожая',
        thermostat__slug='thermostat-hallway',
        thermostat__room=open_space,
        room=open_space,
    )
    ZoneFactory.create(
        slug='bathroom-1f',
        name='Санузел 1 этаж',
        kind=ZoneKind.FLOOR,
        thermostat__slug='thermostat-bathroom-1f',
        thermostat__room=bathroom,
        room=bathroom,
    )
    ZoneFactory.create(
        slug='bathroom-2f',
        name='Санузел 2 этаж',
        kind=ZoneKind.FLOOR,
        thermostat__slug='thermostat-bathroom-2f',
    )
    _sensor('air-fireplace', name='Воздух: у камина', room=open_space)
    _sensor('air-second-floor', name='Воздух: спальня', room=bedroom)
    _sensor('air-attic', name='Воздух: чердак')
    ClimateSettingsFactory.create(outdoor_sensor=_sensor('outdoor', name='Улица'))
    _plug('plug-oven', 'Духовка', monitor_only=True, room=open_space)
    _plug('plug-boiler-1f', 'Бойлер 1 этаж', room=bathroom)
    _plug('plug-boiler-2f', 'Бойлер 2 этаж')
    return _use_case(TemperatureStateStore(unavailable_after=timedelta(minutes=10)), ControlBook())


@pytest.fixture
def f_intent_use_case() -> GetPanelOverviewUseCase:
    ground = FloorFactory.create(slug='floor-1', name='1 этаж', level=1)
    upstairs = FloorFactory.create(slug='floor-2', name='2 этаж', level=2)
    open_space = RoomFactory.create(slug='open-space', name='Гостиная, кухня и прихожая', floor=ground)
    climate = RoomClimateFactory.create(
        room=open_space,
        guest_room=True,
        override_target=Decimal('22.5'),
        override_until=MORNING + timedelta(hours=1),
        override_source='panel',
    )
    ComfortPeriodFactory.create_batch(7, zone=None, room_climate=climate, start=time(6, 30), end=time(23, 30))
    RoomClimateFactory.create(room=RoomFactory.create(slug='bedroom', name='Спальня', floor=upstairs, position=1))
    RoomFactory.create(slug='office', name='Кабинет', floor=upstairs, position=2)
    ZoneFactory.create(
        slug='kitchen',
        name='Кухня',
        thermostat__slug='thermostat-kitchen',
        air_sensor=_sensor('air-kitchen', room=open_space),
        room=open_space,
    )
    ClimateSettingsFactory.create(guests_until=GUESTS_LEAVE)
    return _use_case(TemperatureStateStore(unavailable_after=timedelta(minutes=10)), ControlBook())


@pytest.fixture
def f_fireplace_use_case(request: pytest.FixtureRequest) -> GetPanelOverviewUseCase:
    lit_until, burning, live = request.param
    open_space = RoomFactory.create(
        slug='open-space',
        name='Гостиная, кухня и прихожая',
        floor=FloorFactory.create(slug='floor-1', name='1 этаж', level=1),
    )
    HeatSourceFactory.create(slug='fireplace', name='Камин', room=open_space, live=live, lit_until=lit_until)
    book = ControlBook()
    book.remember_fire('open-space', FireWatch(fire_since=GUARD_AT if burning else None))
    return _use_case(TemperatureStateStore(unavailable_after=timedelta(minutes=10)), book)


@pytest.fixture
def f_pending_use_case(request: pytest.FixtureRequest) -> GetPanelOverviewUseCase:
    setpoint, last_command_at, shed = request.param
    kitchen = _zone('kitchen', 'Кухня', live_control=True, last_command_at=last_command_at)
    thermostats = ThermostatStateStore(unavailable_after=timedelta(minutes=10))
    thermostats.record_state(
        'thermostat-kitchen',
        ThermostatState(
            power=not shed,
            mode=ThermostatMode.HOLD,
            setpoint=Decimal(setpoint),
            floor_temperature=Decimal('24.5'),
            heating=False,
            read_at=MORNING,
        ),
    )
    ZoneLoadFactory.create(slug='kitchen', name='Кухня', zone=kitchen, priority=5, live=True)
    guard_book = GuardBook()
    if shed:
        guard_loads = GuardLoads(
            records=tuple(LoadRepository.all_records()),
            control=LoadSettingsRepository.control(),
            live_zones=frozenset({'kitchen'}),
            meter='phase-meter',
            read_at=GUARD_AT,
        )
        kitchen_shed = replace(BOILER_SHED, load='kitchen', device='thermostat-kitchen', live=True)
        guard_book.remember(
            replace(GuardState.started(guard_loads, GUARD_AT), shedding=ShedState(sheds={'kitchen': kitchen_shed})),
        )
    return GetPanelOverviewUseCase(
        zones=ZoneRepository(),
        intents=_intents(ControlBook()),
        devices=DeviceRepository(),
        house=HouseRepository(),
        climate_settings=ClimateSettingsRepository(),
        learning=ZoneLearningRepository(),
        outages=OutageRepository(),
        overloads=OutageOverloads(journal=JournalRepository()),
        loads=LoadRepository(),
        load_settings=LoadSettingsRepository(),
        thermostat_store=thermostats,
        plug_store=PlugStateStore(unavailable_after=timedelta(minutes=10)),
        temperature_store=TemperatureStateStore(unavailable_after=timedelta(minutes=10)),
        book=ControlBook(),
        guard_book=guard_book,
        clock=ClockStub(trusted=True),
        dry_run=False,
    )


def test_overview_names_the_intent_of_each_room_its_guests_and_whether_it_takes_overrides(
    f_intent_use_case: GetPanelOverviewUseCase,
) -> None:
    output = f_intent_use_case.execute(GetPanelOverviewInputDTO(now=MORNING))

    assert (
        [(room.slug, room.intent, room.adjustable, room.guest_room) for floor in output.floors for room in floor.rooms],
        (output.guests_until, output.guest_rooms),
    ) == (
        [
            (
                'open-space',
                RoomIntent(
                    room='open-space',
                    reason=IntentReason.OVERRIDE,
                    target=Decimal('22.5'),
                    until=MORNING + timedelta(hours=1),
                    forced=(
                        ForcedTarget(target=Decimal('22.0'), beneath=Decimal('22.0')),
                        ForcedTarget(target=Decimal('22.5'), beneath=Decimal('22.0')),
                    ),
                ),
                True,
                True,
            ),
            (
                'bedroom',
                RoomIntent(
                    room='bedroom',
                    reason=IntentReason.SETBACK,
                    target=Decimal('19.5'),
                    until=None,
                    forced=(),
                ),
                False,
                False,
            ),
            ('office', None, False, False),
        ],
        (GUESTS_LEAVE, ('Гостиная, кухня и прихожая',)),
    )


def test_overview_drives_a_zone_to_the_target_its_room_forces_without_a_schedule_change_to_name(
    f_intent_use_case: GetPanelOverviewUseCase,
) -> None:
    kitchen = f_intent_use_case.execute(GetPanelOverviewInputDTO(now=MORNING)).zones[0]

    assert (kitchen.regime, kitchen.mode, kitchen.target_setpoint, kitchen.next_change) == (
        ZoneRegime.COMFORT,
        ScheduleMode.COMFORT,
        Decimal('30.0'),
        None,
    )


def test_overview_leaves_out_guests_gone_or_set_beyond_four_days(f_intent_use_case: GetPanelOverviewUseCase) -> None:
    gone = f_intent_use_case.execute(GetPanelOverviewInputDTO(now=GUESTS_LEAVE))
    far = f_intent_use_case.execute(GetPanelOverviewInputDTO(now=GUESTS_LEAVE - timedelta(days=4, minutes=1)))

    assert (gone.guests_until, far.guests_until, gone.guest_rooms) == (None, None, ('Гостиная, кухня и прихожая',))


@pytest.mark.parametrize(
    ('f_pending_use_case', 'pending'),
    [
        (('28.0', None, False), PendingWrite(kind=PendingKind.HOLDS, at=None)),
        (
            ('22.0', MORNING - timedelta(minutes=10), False),
            PendingWrite(kind=PendingKind.NEXT_WRITE, at=MORNING + timedelta(minutes=20)),
        ),
        (('22.0', None, False), PendingWrite(kind=PendingKind.NEXT_WRITE, at=MORNING)),
        (('22.0', MORNING - timedelta(minutes=10), True), PendingWrite(kind=PendingKind.HELD, at=None)),
    ],
    ids=['thermostat_holds_its_target', 'next_write_in_twenty_minutes', 'next_write_now', 'floor_shed'],
    indirect=['f_pending_use_case'],
)
def test_overview_names_when_each_floor_gets_its_target(
    f_pending_use_case: GetPanelOverviewUseCase,
    pending: PendingWrite,
) -> None:
    zone = f_pending_use_case.execute(GetPanelOverviewInputDTO(now=MORNING)).zones[0]

    assert zone.pending == pending


def test_overview_combines_schedule_and_readings(f_use_case: GetPanelOverviewUseCase) -> None:
    output = f_use_case.execute(GetPanelOverviewInputDTO(now=MORNING))

    kitchen = ZoneOverview(
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
        air_control_live=False,
        path=ControlPath.FLOOR,
        air_target=None,
        regime=ZoneRegime.COMFORT,
        lead=None,
        boost_until=None,
        next_change=ScheduleTransition(
            mode=ScheduleMode.NIGHT,
            at=datetime(2026, 10, 29, 23, 30, tzinfo=MOSCOW),
        ),
        air=None,
        pending=PendingWrite(kind=PendingKind.WATCHING, at=None),
    )
    assert output == GetPanelOverviewOutputDTO(
        zones=(kitchen,),
        plugs=(),
        loads=(),
        sensors=(),
        outdoor=None,
        standalone_sensors=(),
        floors=(),
        unplaced_zones=(kitchen,),
        unplaced_plugs=(),
        outages=(),
        clock=ClockStatus(trusted=True, code=ClockStatusCode.NTP_SYNCHRONIZED, reason='stub'),
        dry_run=True,
    )


def test_overview_shows_unread_thermostat_as_unavailable(f_offline_use_case: GetPanelOverviewUseCase) -> None:
    output = f_offline_use_case.execute(GetPanelOverviewInputDTO(now=MORNING))

    hallway = ZoneOverview(
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
        air_control_live=False,
        path=ControlPath.FLOOR,
        air_target=None,
        regime=ZoneRegime.COMFORT,
        lead=None,
        boost_until=None,
        next_change=None,
        air=None,
        pending=PendingWrite(kind=PendingKind.UNREACHED, at=None),
    )
    assert output == GetPanelOverviewOutputDTO(
        zones=(hallway,),
        plugs=(),
        loads=(),
        sensors=(),
        outdoor=None,
        standalone_sensors=(),
        floors=(),
        unplaced_zones=(hallway,),
        unplaced_plugs=(),
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


def test_overview_gives_a_floor_mode_preheat_its_fixed_lead(f_use_case: GetPanelOverviewUseCase) -> None:
    zone = f_use_case.execute(GetPanelOverviewInputDTO(now=WARM_UP)).zones[0]

    assert (zone.path, zone.regime, zone.lead, zone.next_change) == (
        ControlPath.FLOOR,
        ZoneRegime.PREHEAT,
        timedelta(hours=3),
        ScheduleTransition(mode=ScheduleMode.COMFORT, at=COMFORT_AT),
    )


def test_overview_names_the_path_that_drives_each_zone(f_paths_use_case: GetPanelOverviewUseCase) -> None:
    output = f_paths_use_case.execute(GetPanelOverviewInputDTO(now=MORNING))

    assert [
        (zone.slug, zone.path, zone.air_control_live, zone.air_target, zone.target_setpoint) for zone in output.zones
    ] == [
        ('living-room', ControlPath.AIR, True, Decimal('22.0'), Decimal('30.0')),
        ('kitchen', ControlPath.AIR_DRY_RUN, False, Decimal('22.0'), Decimal('28.0')),
        ('hallway', ControlPath.FLOOR, True, None, Decimal('28.0')),
        ('bathroom-2f', ControlPath.FLOOR, False, None, Decimal('28.0')),
    ]


@pytest.mark.parametrize(
    ('now', 'regime', 'air_target', 'target_setpoint', 'lead', 'next_change'),
    [
        (
            NIGHT,
            ZoneRegime.NIGHT,
            Decimal('19.5'),
            Decimal('18.0'),
            None,
            ScheduleTransition(mode=ScheduleMode.PREHEAT, at=COMFORT_AT - LEARNED_LEAD),
        ),
        (
            WARM_UP,
            ZoneRegime.PREHEAT,
            Decimal('22.0'),
            Decimal('30.0'),
            LEARNED_LEAD,
            ScheduleTransition(mode=ScheduleMode.COMFORT, at=COMFORT_AT),
        ),
    ],
    ids=['night', 'preheat'],
)
def test_overview_times_the_warm_up_of_a_zone_in_air_mode_by_its_optimal_lead(
    f_learned_use_case: GetPanelOverviewUseCase,
    now: datetime,
    regime: ZoneRegime,
    air_target: Decimal,
    target_setpoint: Decimal,
    lead: timedelta | None,
    next_change: ScheduleTransition,
) -> None:
    zone = f_learned_use_case.execute(GetPanelOverviewInputDTO(now=now)).zones[0]

    assert (zone.path, zone.regime, zone.air_target, zone.target_setpoint, zone.lead, zone.next_change) == (
        ControlPath.AIR,
        regime,
        air_target,
        target_setpoint,
        lead,
        next_change,
    )


def test_overview_keeps_the_preheat_start_the_heating_cycle_holds(
    f_held_preheat_use_case: GetPanelOverviewUseCase,
) -> None:
    zone = f_held_preheat_use_case.execute(GetPanelOverviewInputDTO(now=LATE_WARM_UP)).zones[0]

    assert (zone.regime, zone.lead, zone.target_setpoint) == (ZoneRegime.PREHEAT, LEARNED_LEAD, Decimal('30.0'))


@pytest.mark.parametrize(
    ('f_backup_use_case', 'target'),
    [(True, Decimal('28.0')), (False, Decimal('22.0'))],
    ids=['live_raises_to_comfort', 'gate_off_keeps_night'],
    indirect=['f_backup_use_case'],
)
def test_overview_shows_the_target_backup_heat_raises_only_while_its_gate_is_on(
    f_backup_use_case: GetPanelOverviewUseCase,
    target: Decimal,
) -> None:
    zone = f_backup_use_case.execute(GetPanelOverviewInputDTO(now=NIGHT)).zones[0]

    assert (zone.regime, zone.target_setpoint) == (ZoneRegime.NIGHT, target)


def test_overview_gives_each_room_its_comfort_and_its_backup_heat(
    f_room_comfort_use_case: GetPanelOverviewUseCase,
) -> None:
    output = f_room_comfort_use_case.execute(GetPanelOverviewInputDTO(now=NIGHT))

    assert (
        [(room.slug, room.comfort) for floor in output.floors for room in floor.rooms],
        output.zones[0].target_setpoint,
    ) == (
        [
            (
                'open-space',
                RoomComfortOverview(
                    comfort_target=Decimal('22.0'),
                    setback_target=Decimal('19.0'),
                    air_sensor=None,
                    backup_zone=None,
                    backup_zone_name=None,
                    backup_below=Decimal('19.0'),
                    backup_live=False,
                    backup_active=False,
                ),
            ),
            (
                'bedroom',
                RoomComfortOverview(
                    comfort_target=Decimal('20.5'),
                    setback_target=Decimal('19.5'),
                    air_sensor='air-second-floor',
                    backup_zone='bathroom-2f',
                    backup_zone_name='Санузел 2 этаж',
                    backup_below=Decimal('19.0'),
                    backup_live=False,
                    backup_active=True,
                ),
            ),
            ('office', None),
        ],
        Decimal('22.0'),
    )


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


def test_overview_reads_the_air_of_zones_outdoors_and_elsewhere(f_sensors_use_case: GetPanelOverviewUseCase) -> None:
    output = f_sensors_use_case.execute(GetPanelOverviewInputDTO(now=MORNING))

    air_second_floor = SensorOverview(
        slug='air-second-floor',
        name='Воздух: 2 этаж',
        kind=DeviceKind.TEMPERATURE_SENSOR,
        available=True,
        error_code=None,
        error=None,
        temperature=Decimal('19.8'),
        humidity=Decimal('38.0'),
        age_seconds=12,
    )
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
    air_hallway = SensorOverview(
        slug='air-hallway',
        name='Воздух: прихожая',
        kind=DeviceKind.TEMPERATURE_SENSOR,
        available=False,
        error_code='broker_unavailable',
        error='connection refused',
        temperature=None,
        humidity=None,
        age_seconds=None,
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
        age_seconds=12,
    )
    assert (
        output.sensors,
        [(zone.name, zone.air) for zone in output.zones],
        output.outdoor,
        output.standalone_sensors,
    ) == (
        (air_second_floor, air_kitchen, air_hallway, outdoor),
        [('Кухня', air_kitchen), ('Прихожая', air_hallway), ('Санузел 1 этаж', None)],
        outdoor,
        (air_second_floor,),
    )


def test_overview_lists_a_sensor_outside_zones_and_climate_settings_with_the_air(
    f_unplaced_sensor_use_case: GetPanelOverviewUseCase,
) -> None:
    output = f_unplaced_sensor_use_case.execute(GetPanelOverviewInputDTO(now=MORNING))

    assert (output.outdoor, [sensor.slug for sensor in output.standalone_sensors]) == (None, ['outdoor'])


def test_overview_groups_zones_sensors_and_plugs_by_floor_and_room(f_house_use_case: GetPanelOverviewUseCase) -> None:
    output = f_house_use_case.execute(GetPanelOverviewInputDTO(now=MORNING))

    assert [
        (
            floor.slug,
            floor.name,
            [
                (
                    room.slug,
                    room.floor,
                    [zone.slug for zone in room.zones],
                    [sensor.slug for sensor in room.sensors],
                    [plug.slug for plug in room.plugs],
                    room.devices,
                )
                for room in floor.rooms
            ],
        )
        for floor in output.floors
    ] == [
        (
            'floor-1',
            '1 этаж',
            [
                (
                    'open-space',
                    'floor-1',
                    ['kitchen', 'hallway'],
                    ['air-fireplace'],
                    ['plug-oven'],
                    ('air-fireplace', 'air-kitchen', 'plug-oven', 'thermostat-hallway', 'thermostat-kitchen'),
                ),
                (
                    'bathroom-1f',
                    'floor-1',
                    ['bathroom-1f'],
                    [],
                    ['plug-boiler-1f'],
                    ('plug-boiler-1f', 'thermostat-bathroom-1f'),
                ),
            ],
        ),
        (
            'floor-2',
            '2 этаж',
            [
                ('bedroom', 'floor-2', [], ['air-second-floor'], [], ('air-second-floor',)),
                ('office', 'floor-2', [], [], [], ()),
            ],
        ),
    ]


def test_overview_keeps_a_room_with_nothing_to_show_for_the_api(f_house_use_case: GetPanelOverviewUseCase) -> None:
    upstairs = f_house_use_case.execute(GetPanelOverviewInputDTO(now=MORNING)).floors[1]

    assert ([(room.slug, room.shown) for room in upstairs.rooms], [room.slug for room in upstairs.shown_rooms]) == (
        [('bedroom', True), ('office', False)],
        ['bedroom'],
    )


def test_overview_lists_zones_and_plugs_without_a_room_as_unplaced(f_house_use_case: GetPanelOverviewUseCase) -> None:
    output = f_house_use_case.execute(GetPanelOverviewInputDTO(now=MORNING))

    assert (
        [zone.slug for zone in output.unplaced_zones],
        [plug.slug for plug in output.unplaced_plugs],
        [(zone.slug, zone.room) for zone in output.zones],
        [(plug.slug, plug.room) for plug in output.plugs],
    ) == (
        ['bathroom-2f'],
        ['plug-boiler-2f'],
        [
            ('kitchen', 'open-space'),
            ('hallway', 'open-space'),
            ('bathroom-1f', 'bathroom-1f'),
            ('bathroom-2f', None),
        ],
        [('plug-boiler-1f', 'bathroom-1f'), ('plug-boiler-2f', None), ('plug-oven', 'open-space')],
    )


def test_a_sensor_placed_in_a_room_leaves_the_air_row(f_house_use_case: GetPanelOverviewUseCase) -> None:
    output = f_house_use_case.execute(GetPanelOverviewInputDTO(now=MORNING))

    assert (
        [sensor.slug for sensor in output.standalone_sensors],
        [(sensor.slug, sensor.room) for sensor in output.sensors],
    ) == (
        ['air-attic'],
        [
            ('air-kitchen', 'open-space'),
            ('air-second-floor', 'bedroom'),
            ('air-fireplace', 'open-space'),
            ('air-attic', None),
            ('outdoor', None),
        ],
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


@pytest.mark.usefixtures('overload_before_the_night_cut')
def test_overview_notes_an_overload_journaled_right_before_a_cut(
    f_outages_use_case: GetPanelOverviewUseCase,
    f_outages: list[Outage],
) -> None:
    output = f_outages_use_case.execute(GetPanelOverviewInputDTO(now=MORNING))

    assert [(outage.id, outage.overload) for outage in output.outages] == [
        (f_outages[2].pk, PrecedingOverload(phase=LoadPhase.L2, amps=Decimal('26.4'))),
        (f_outages[0].pk, None),
    ]


def test_overview_lists_the_loads_by_priority_with_their_phase_current_and_state(
    f_loads_use_case: GetPanelOverviewUseCase,
) -> None:
    output = f_loads_use_case.execute(GetPanelOverviewInputDTO(now=MORNING))

    assert output.loads == (
        LoadOverview(
            slug='boiler-1f',
            name='Бойлер 1 этаж',
            kind=LoadKind.PLUG,
            zone=None,
            device='plug-boiler-1f',
            priority=1,
            phase=LoadPhase.L2,
            phase_origin=PhaseOrigin.SET,
            learned_phase=None,
            learned_amps=None,
            learning_samples=0,
            nominal_amps=Decimal('6.0'),
            amps=Decimal('6.0'),
            amps_origin=AmpsOrigin.NOMINAL,
            state=LoadState.SHED_DRY_RUN,
            live=False,
        ),
        LoadOverview(
            slug='boiler-2f',
            name='Бойлер 2 этаж',
            kind=LoadKind.PLUG,
            zone=None,
            device='plug-boiler-2f',
            priority=2,
            phase=LoadPhase.L3,
            phase_origin=PhaseOrigin.LEARNED,
            learned_phase=LoadPhase.L3,
            learned_amps=Decimal('6.4'),
            learning_samples=3,
            nominal_amps=None,
            amps=Decimal('6.4'),
            amps_origin=AmpsOrigin.LEARNED,
            state=LoadState.IDLE,
            live=True,
        ),
        LoadOverview(
            slug='hallway',
            name='Прихожая',
            kind=LoadKind.ZONE,
            zone='hallway',
            device='thermostat-hallway',
            priority=4,
            phase=LoadPhase.UNKNOWN,
            phase_origin=PhaseOrigin.UNKNOWN,
            learned_phase=None,
            learned_amps=None,
            learning_samples=0,
            nominal_amps=None,
            amps=Decimal('6.0'),
            amps_origin=AmpsOrigin.DEFAULT,
            state=LoadState.UNAVAILABLE,
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
            state=LoadState.WAITING,
            live=True,
        ),
        LoadOverview(
            slug='living-room',
            name='Гостиная',
            kind=LoadKind.ZONE,
            zone='living-room',
            device='thermostat-living-room',
            priority=7,
            phase=LoadPhase.L1,
            phase_origin=PhaseOrigin.SET,
            learned_phase=None,
            learned_amps=None,
            learning_samples=0,
            nominal_amps=Decimal('8.0'),
            amps=Decimal('8.0'),
            amps_origin=AmpsOrigin.NOMINAL,
            state=LoadState.DRAWING,
            live=False,
        ),
    )


@pytest.mark.parametrize(
    ('f_fireplace_use_case', 'lit_until', 'burning', 'live'),
    [
        ((MORNING + timedelta(hours=2), False, False), MORNING + timedelta(hours=2), False, False),
        ((MORNING - timedelta(minutes=1), True, True), None, True, True),
        ((MORNING + timedelta(hours=5), True, False), None, True, False),
    ],
    ids=['marked', 'mark_ended_while_the_detector_sees_a_fire', 'mark_beyond_four_hours_of_a_clock_set_back'],
    indirect=['f_fireplace_use_case'],
)
def test_overview_shows_the_fireplace_of_a_room_with_its_mark_and_what_the_detector_sees(
    f_fireplace_use_case: GetPanelOverviewUseCase,
    lit_until: datetime | None,
    burning: bool,
    live: bool,
) -> None:
    [open_space] = f_fireplace_use_case.execute(GetPanelOverviewInputDTO(now=MORNING)).floors[0].rooms

    assert (open_space.sources, open_space.shown) == (
        (
            SourceOverview(
                slug='fireplace',
                name='Камин',
                kind=HeatSourceKind.FIREPLACE,
                live=live,
                lit_until=lit_until,
                burning=burning,
            ),
        ),
        True,
    )
