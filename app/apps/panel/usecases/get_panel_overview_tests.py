from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.climate.domain.memory import ZoneMemory
from apps.climate.domain.modes import AirState, ControlPath, ScheduleMode, ZoneKind
from apps.climate.domain.optimal_start import PreheatStart
from apps.climate.domain.schedule import ScheduleTransition
from apps.climate.factories import ClimateSettingsFactory, ComfortPeriodFactory, ZoneFactory, ZoneLearningFactory
from apps.climate.models import Zone
from apps.climate.repositories.climate_settings import ClimateSettingsRepository
from apps.climate.repositories.zone import ZoneRepository
from apps.climate.repositories.zone_learning import ZoneLearningRepository
from apps.climate.services.control_book import ControlBook
from apps.core.stubs import MonotonicClockStub
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.domain.readings import PlugState, TemperatureReading, ThermostatMode, ThermostatState
from apps.devices.factories import DeviceFactory
from apps.devices.models import Device
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.state_store import PlugStateStore, TemperatureStateStore, ThermostatStateStore
from apps.panel.usecases.get_panel_overview import (
    GetPanelOverviewInputDTO,
    GetPanelOverviewOutputDTO,
    GetPanelOverviewUseCase,
    PlugOverview,
    SensorOverview,
    ZoneOverview,
    ZoneRegime,
)
from apps.power.domain.loads import LoadPhase
from apps.power.domain.outage import OutageCause, OutageRecord, PrecedingOverload
from apps.power.factories import OutageFactory
from apps.power.models import Outage
from apps.power.repositories.outage import OutageRepository
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


def _sensor(slug: str) -> Device:
    return DeviceFactory.create(slug=slug, kind=DeviceKind.TEMPERATURE_SENSOR, driver=DeviceDriver.ESPHOME_TEMPERATURE)


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


def _use_case(temperatures: TemperatureStateStore, book: ControlBook) -> GetPanelOverviewUseCase:
    return GetPanelOverviewUseCase(
        zones=ZoneRepository(),
        devices=DeviceRepository(),
        climate_settings=ClimateSettingsRepository(),
        learning=ZoneLearningRepository(),
        outages=OutageRepository(),
        overloads=OutageOverloads(journal=JournalRepository()),
        thermostat_store=ThermostatStateStore(unavailable_after=timedelta(minutes=10)),
        plug_store=PlugStateStore(unavailable_after=timedelta(minutes=10)),
        temperature_store=temperatures,
        book=book,
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
        devices=DeviceRepository(),
        climate_settings=ClimateSettingsRepository(),
        learning=ZoneLearningRepository(),
        outages=OutageRepository(),
        overloads=OutageOverloads(journal=JournalRepository()),
        thermostat_store=store,
        plug_store=PlugStateStore(unavailable_after=timedelta(minutes=10)),
        temperature_store=TemperatureStateStore(unavailable_after=timedelta(minutes=10)),
        book=ControlBook(),
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
        climate_settings=ClimateSettingsRepository(),
        learning=ZoneLearningRepository(),
        outages=OutageRepository(),
        overloads=OutageOverloads(journal=JournalRepository()),
        thermostat_store=store,
        plug_store=PlugStateStore(unavailable_after=timedelta(minutes=10)),
        temperature_store=TemperatureStateStore(unavailable_after=timedelta(minutes=10)),
        book=ControlBook(),
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
        climate_settings=ClimateSettingsRepository(),
        learning=ZoneLearningRepository(),
        outages=OutageRepository(),
        overloads=OutageOverloads(journal=JournalRepository()),
        thermostat_store=ThermostatStateStore(unavailable_after=timedelta(minutes=10)),
        plug_store=PlugStateStore(unavailable_after=timedelta(minutes=10)),
        temperature_store=TemperatureStateStore(unavailable_after=timedelta(minutes=10)),
        book=ControlBook(),
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
        climate_settings=ClimateSettingsRepository(),
        learning=ZoneLearningRepository(),
        outages=OutageRepository(),
        overloads=OutageOverloads(journal=JournalRepository()),
        thermostat_store=ThermostatStateStore(unavailable_after=timedelta(minutes=10)),
        plug_store=PlugStateStore(unavailable_after=timedelta(minutes=10)),
        temperature_store=TemperatureStateStore(unavailable_after=timedelta(minutes=10)),
        book=ControlBook(),
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
        climate_settings=ClimateSettingsRepository(),
        learning=ZoneLearningRepository(),
        outages=OutageRepository(),
        overloads=OutageOverloads(journal=JournalRepository()),
        thermostat_store=ThermostatStateStore(unavailable_after=timedelta(minutes=10)),
        plug_store=PlugStateStore(unavailable_after=timedelta(minutes=10)),
        temperature_store=TemperatureStateStore(unavailable_after=timedelta(minutes=10)),
        book=ControlBook(),
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
        climate_settings=ClimateSettingsRepository(),
        learning=ZoneLearningRepository(),
        outages=OutageRepository(),
        overloads=OutageOverloads(journal=JournalRepository()),
        thermostat_store=ThermostatStateStore(unavailable_after=timedelta(minutes=10)),
        plug_store=plug_store,
        temperature_store=TemperatureStateStore(unavailable_after=timedelta(minutes=10)),
        book=ControlBook(),
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
    ClimateSettingsFactory.create(outdoor_sensor=sensors['outdoor'], second_floor_sensor=sensors['air-second-floor'])
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
        devices=DeviceRepository(),
        climate_settings=ClimateSettingsRepository(),
        learning=ZoneLearningRepository(),
        outages=OutageRepository(),
        overloads=OutageOverloads(journal=JournalRepository()),
        thermostat_store=ThermostatStateStore(unavailable_after=timedelta(minutes=10)),
        plug_store=PlugStateStore(unavailable_after=timedelta(minutes=10)),
        temperature_store=store,
        book=ControlBook(),
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
        devices=DeviceRepository(),
        climate_settings=ClimateSettingsRepository(),
        learning=ZoneLearningRepository(),
        outages=OutageRepository(),
        overloads=OutageOverloads(journal=JournalRepository()),
        thermostat_store=ThermostatStateStore(unavailable_after=timedelta(minutes=10)),
        plug_store=PlugStateStore(unavailable_after=timedelta(minutes=10)),
        temperature_store=TemperatureStateStore(unavailable_after=timedelta(minutes=10)),
        book=ControlBook(),
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
def f_guard_use_case() -> GetPanelOverviewUseCase:
    zone = _zone('bathroom-2f', 'Санузел 2 этаж', kind=ZoneKind.FLOOR)
    ClimateSettingsFactory.create(second_floor_guard_zone=zone, second_floor_guard_live=True)
    book = ControlBook()
    book.remember_guard(True)
    return _use_case(TemperatureStateStore(unavailable_after=timedelta(minutes=10)), book)


@pytest.fixture
def f_guard_dry_run_use_case() -> GetPanelOverviewUseCase:
    zone = _zone('bathroom-2f', 'Санузел 2 этаж', kind=ZoneKind.FLOOR)
    ClimateSettingsFactory.create(second_floor_guard_zone=zone)
    book = ControlBook()
    book.remember_guard(True)
    return _use_case(TemperatureStateStore(unavailable_after=timedelta(minutes=10)), book)


@pytest.fixture
def f_outages_use_case() -> GetPanelOverviewUseCase:
    return GetPanelOverviewUseCase(
        zones=ZoneRepository(),
        devices=DeviceRepository(),
        climate_settings=ClimateSettingsRepository(),
        learning=ZoneLearningRepository(),
        outages=OutageRepository(),
        overloads=OutageOverloads(journal=JournalRepository()),
        thermostat_store=ThermostatStateStore(unavailable_after=timedelta(minutes=10)),
        plug_store=PlugStateStore(unavailable_after=timedelta(minutes=10)),
        temperature_store=TemperatureStateStore(unavailable_after=timedelta(minutes=10)),
        book=ControlBook(),
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
            ),
        ),
        plugs=(),
        sensors=(),
        outdoor=None,
        standalone_sensors=(),
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
                air_control_live=False,
                path=ControlPath.FLOOR,
                air_target=None,
                regime=ZoneRegime.COMFORT,
                lead=None,
                boost_until=None,
                next_change=None,
                air=None,
            ),
        ),
        plugs=(),
        sensors=(),
        outdoor=None,
        standalone_sensors=(),
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


def test_overview_shows_the_target_the_live_second_floor_guard_raises(
    f_guard_use_case: GetPanelOverviewUseCase,
) -> None:
    zone = f_guard_use_case.execute(GetPanelOverviewInputDTO(now=NIGHT)).zones[0]

    assert (zone.regime, zone.target_setpoint) == (ZoneRegime.NIGHT, Decimal('28.0'))


def test_overview_keeps_the_night_target_while_the_guard_runs_in_dry_run(
    f_guard_dry_run_use_case: GetPanelOverviewUseCase,
) -> None:
    zone = f_guard_dry_run_use_case.execute(GetPanelOverviewInputDTO(now=NIGHT)).zones[0]

    assert (zone.regime, zone.target_setpoint) == (ZoneRegime.NIGHT, Decimal('22.0'))


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
