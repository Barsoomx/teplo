from datetime import timedelta
from decimal import Decimal

import pytest

from apps.climate.domain.air_policy import AirRegulationSettings
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
        preheat_lead_cap_minutes=210,
        learned_mornings_needed=5,
        second_floor_guard_zone=ZoneFactory.create(slug='bathroom-2f'),
        second_floor_guard_threshold=Decimal('18.5'),
        second_floor_guard_hysteresis=Decimal('0.50'),
        second_floor_guard_live=True,
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
        regulation=AirRegulationSettings(start_offset=Decimal('0.25'), initial_early_stop_offset=Decimal('0.40')),
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
        regulation=AirRegulationSettings(start_offset=Decimal('0.3'), initial_early_stop_offset=Decimal('0.3')),
        optimal_start=OptimalStartSettings(lead_cap=timedelta(hours=4), mornings_needed=3),
        second_floor_guard=SecondFloorGuardSettings(
            zone_slug=None,
            threshold=Decimal('19.0'),
            hysteresis=Decimal('0.3'),
            live=False,
        ),
    )
