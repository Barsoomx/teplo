from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError, connection
from django.test.utils import CaptureQueriesContext

from apps.climate.domain.air_policy import AirRegulationSettings
from apps.climate.domain.climate_config import ClimateConfig, ClimateConfigRecord
from apps.climate.domain.control import ClimateControl
from apps.climate.domain.optimal_start import OptimalStartSettings
from apps.climate.domain.second_floor_guard import SecondFloorGuardSettings
from apps.climate.domain.sensors import ClimateSensors
from apps.climate.factories import ClimateSettingsFactory, ZoneFactory
from apps.climate.models import ClimateSettings
from apps.climate.repositories.climate_settings import ClimateSettingsRepository
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.factories import DeviceFactory
from apps.devices.models import Device

pytestmark = pytest.mark.django_db

DEFAULT_CONFIG = ClimateConfig(
    air_start_offset=Decimal('0.30'),
    initial_early_stop_offset=Decimal('0.30'),
    air_min_band=Decimal('0.20'),
    learned_mornings_needed=3,
    preheat_lead_cap_minutes=240,
    second_floor_guard_threshold=Decimal('19.0'),
    second_floor_guard_hysteresis=Decimal('0.30'),
)


@pytest.fixture
def f_sensors() -> list[Device]:
    sensors = [
        DeviceFactory.create(
            slug='outdoor',
            kind=DeviceKind.TEMPERATURE_SENSOR,
            driver=DeviceDriver.ESPHOME_TEMPERATURE,
        ),
        DeviceFactory.create(
            slug='air-second-floor',
            kind=DeviceKind.TEMPERATURE_SENSOR,
            driver=DeviceDriver.ESPHOME_TEMPERATURE,
        ),
    ]
    ClimateSettingsFactory.create(outdoor_sensor=sensors[0], second_floor_sensor=sensors[1])
    return sensors


@pytest.fixture
def f_outdoor_sensor() -> Device:
    sensor = DeviceFactory.create(
        slug='outdoor',
        kind=DeviceKind.TEMPERATURE_SENSOR,
        driver=DeviceDriver.ESPHOME_TEMPERATURE,
    )
    ClimateSettingsFactory.create(outdoor_sensor=sensor)
    return sensor


@pytest.fixture
def tuned_settings() -> ClimateSettings:
    return ClimateSettingsFactory.create(
        outdoor_sensor=DeviceFactory.create(
            slug='outdoor',
            kind=DeviceKind.TEMPERATURE_SENSOR,
            driver=DeviceDriver.ESPHOME_TEMPERATURE,
        ),
        air_start_offset=Decimal('0.25'),
        initial_early_stop_offset=Decimal('0.40'),
        air_min_band=Decimal('0.15'),
        preheat_lead_cap_minutes=210,
        learned_mornings_needed=5,
        second_floor_guard_zone=ZoneFactory.create(slug='bathroom-2f'),
        second_floor_guard_threshold=Decimal('18.5'),
        second_floor_guard_hysteresis=Decimal('0.50'),
        second_floor_guard_live=True,
    )


@pytest.fixture
def f_guarded_settings() -> ClimateSettings:
    return ClimateSettingsFactory.create(
        second_floor_guard_zone=ZoneFactory.create(slug='bathroom-2f'),
        second_floor_guard_live=True,
        learned_mornings_needed=5,
        air_min_band=Decimal('0.25'),
    )


def test_sensors_name_the_outdoor_and_second_floor_sensors(f_sensors: list[Device]) -> None:
    assert ClimateSettingsRepository.sensors() == ClimateSensors(
        outdoor_slug=f_sensors[0].slug,
        second_floor_slug=f_sensors[1].slug,
    )


def test_sensor_left_empty_is_none(f_outdoor_sensor: Device) -> None:
    assert ClimateSettingsRepository.sensors() == ClimateSensors(
        outdoor_slug=f_outdoor_sensor.slug,
        second_floor_slug=None,
    )


def test_without_climate_settings_no_sensor_is_named() -> None:
    assert ClimateSettingsRepository.sensors() == ClimateSensors(outdoor_slug=None, second_floor_slug=None)


@pytest.mark.usefixtures('tuned_settings')
def test_control_reads_regulation_optimal_start_and_guard() -> None:
    assert ClimateSettingsRepository.control() == ClimateControl(
        sensors=ClimateSensors(outdoor_slug='outdoor', second_floor_slug=None),
        regulation=AirRegulationSettings(
            start_offset=Decimal('0.25'),
            initial_early_stop_offset=Decimal('0.40'),
            min_band=Decimal('0.15'),
        ),
        optimal_start=OptimalStartSettings(lead_cap=timedelta(minutes=210), mornings_needed=5),
        second_floor_guard=SecondFloorGuardSettings(
            zone_slug='bathroom-2f',
            threshold=Decimal('18.5'),
            hysteresis=Decimal('0.50'),
            live=True,
        ),
    )


def test_control_without_climate_settings_takes_defaults() -> None:
    assert ClimateSettingsRepository.control() == ClimateControl(
        sensors=ClimateSensors(outdoor_slug=None, second_floor_slug=None),
        regulation=AirRegulationSettings(
            start_offset=Decimal('0.3'),
            initial_early_stop_offset=Decimal('0.3'),
            min_band=Decimal('0.2'),
        ),
        optimal_start=OptimalStartSettings(lead_cap=timedelta(hours=4), mornings_needed=3),
        second_floor_guard=SecondFloorGuardSettings(
            zone_slug=None,
            threshold=Decimal('19.0'),
            hysteresis=Decimal('0.3'),
            live=False,
        ),
    )


def test_config_record_without_climate_settings_takes_the_defaults() -> None:
    assert ClimateSettingsRepository.config_record() == ClimateConfigRecord(
        id=None,
        config=DEFAULT_CONFIG,
        second_floor_guard_live=False,
    )


def test_config_record_reads_the_stored_settings_and_the_guard_gate(f_guarded_settings: ClimateSettings) -> None:
    assert ClimateSettingsRepository.config_record() == ClimateConfigRecord(
        id=f_guarded_settings.pk,
        config=replace(DEFAULT_CONFIG, learned_mornings_needed=5, air_min_band=Decimal('0.25')),
        second_floor_guard_live=True,
    )


def test_saved_config_writes_the_named_fields_and_keeps_the_guard(f_guarded_settings: ClimateSettings) -> None:
    config = replace(DEFAULT_CONFIG, air_start_offset=Decimal('0.40'), preheat_lead_cap_minutes=200)

    ClimateSettingsRepository.save_config(config, ('air_start_offset',))

    stored = ClimateSettings.objects.get(pk=f_guarded_settings.pk)
    assert (
        stored.air_start_offset,
        stored.preheat_lead_cap_minutes,
        stored.learned_mornings_needed,
        stored.second_floor_guard_live,
        stored.second_floor_guard_zone_id,
    ) == (Decimal('0.40'), 240, 5, True, f_guarded_settings.second_floor_guard_zone_id)


def test_saved_config_writes_the_min_band(f_guarded_settings: ClimateSettings) -> None:
    ClimateSettingsRepository.save_config(
        replace(DEFAULT_CONFIG, air_min_band=Decimal('0.40'), air_start_offset=Decimal('0.50')),
        ('air_min_band', 'air_start_offset'),
    )

    stored = ClimateSettings.objects.get(pk=f_guarded_settings.pk)
    assert (stored.air_min_band, stored.air_start_offset, stored.second_floor_guard_live) == (
        Decimal('0.40'),
        Decimal('0.50'),
        True,
    )


def test_saved_config_without_stored_settings_creates_them_with_the_guard_off() -> None:
    ClimateSettingsRepository.save_config(
        replace(DEFAULT_CONFIG, learned_mornings_needed=4),
        ('learned_mornings_needed',),
    )

    stored = ClimateSettings.objects.get()
    assert (stored.learned_mornings_needed, stored.air_start_offset, stored.second_floor_guard_live) == (
        4,
        Decimal('0.30'),
        False,
    )


def test_saved_config_refuses_to_write_the_guard_gate() -> None:
    with pytest.raises(KeyError):
        ClimateSettingsRepository.save_config(DEFAULT_CONFIG, ('second_floor_guard_live',))


def test_config_record_for_update_takes_the_write_lock_before_it_reads_the_settings(
    f_guarded_settings: ClimateSettings,
) -> None:
    with CaptureQueriesContext(connection) as queries:
        record = ClimateSettingsRepository.config_record_for_update()

    assert (record.id, [query['sql'].split(' ', 1)[0] for query in queries.captured_queries]) == (
        f_guarded_settings.pk,
        ['UPDATE', 'SELECT'],
    )


@pytest.mark.parametrize(
    'overrides',
    [{'air_start_offset': Decimal('0.15')}, {'air_min_band': Decimal('0.35')}],
    ids=['start_offset_narrowed', 'band_widened'],
)
def test_database_refuses_a_start_offset_below_the_min_band(overrides: dict[str, Decimal]) -> None:
    with pytest.raises(IntegrityError):
        ClimateSettingsFactory.create(**overrides)


def test_start_offset_may_equal_the_min_band() -> None:
    ClimateSettingsFactory.create(air_start_offset=Decimal('0.20'))

    assert ClimateSettingsRepository.control().regulation.start_offset == Decimal('0.20')
