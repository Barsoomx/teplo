from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.climate.domain.modes import ScheduleMode, ZoneKind
from apps.climate.domain.schedule import ScheduleTransition
from apps.climate.factories import ClimateSettingsFactory, ComfortPeriodFactory, ZoneFactory
from apps.climate.repositories.climate_settings import ClimateSettingsRepository
from apps.climate.repositories.zone import ZoneRepository
from apps.core.stubs import MonotonicClockStub
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.domain.readings import PlugState, TemperatureReading, ThermostatMode, ThermostatState
from apps.devices.factories import DeviceFactory
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
    return GetPanelOverviewUseCase(
        zones=ZoneRepository(),
        devices=DeviceRepository(),
        climate_settings=ClimateSettingsRepository(),
        thermostat_store=store,
        plug_store=PlugStateStore(unavailable_after=timedelta(minutes=10)),
        temperature_store=TemperatureStateStore(unavailable_after=timedelta(minutes=10)),
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
        thermostat_store=store,
        plug_store=PlugStateStore(unavailable_after=timedelta(minutes=10)),
        temperature_store=TemperatureStateStore(unavailable_after=timedelta(minutes=10)),
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
        thermostat_store=ThermostatStateStore(unavailable_after=timedelta(minutes=10)),
        plug_store=PlugStateStore(unavailable_after=timedelta(minutes=10)),
        temperature_store=TemperatureStateStore(unavailable_after=timedelta(minutes=10)),
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
        thermostat_store=ThermostatStateStore(unavailable_after=timedelta(minutes=10)),
        plug_store=PlugStateStore(unavailable_after=timedelta(minutes=10)),
        temperature_store=TemperatureStateStore(unavailable_after=timedelta(minutes=10)),
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
        thermostat_store=ThermostatStateStore(unavailable_after=timedelta(minutes=10)),
        plug_store=PlugStateStore(unavailable_after=timedelta(minutes=10)),
        temperature_store=TemperatureStateStore(unavailable_after=timedelta(minutes=10)),
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
        thermostat_store=ThermostatStateStore(unavailable_after=timedelta(minutes=10)),
        plug_store=plug_store,
        temperature_store=TemperatureStateStore(unavailable_after=timedelta(minutes=10)),
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
        thermostat_store=ThermostatStateStore(unavailable_after=timedelta(minutes=10)),
        plug_store=PlugStateStore(unavailable_after=timedelta(minutes=10)),
        temperature_store=store,
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
        thermostat_store=ThermostatStateStore(unavailable_after=timedelta(minutes=10)),
        plug_store=PlugStateStore(unavailable_after=timedelta(minutes=10)),
        temperature_store=TemperatureStateStore(unavailable_after=timedelta(minutes=10)),
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
                air=None,
            ),
        ),
        plugs=(),
        sensors=(),
        outdoor=None,
        standalone_sensors=(),
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
                air=None,
            ),
        ),
        plugs=(),
        sensors=(),
        outdoor=None,
        standalone_sensors=(),
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
