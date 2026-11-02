import pytest

from apps.climate.domain.sensors import ClimateSensors
from apps.climate.factories import ClimateSettingsFactory
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
