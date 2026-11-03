from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError, connection
from django.test.utils import CaptureQueriesContext

from apps.climate.domain.air_policy import AirRegulationSettings
from apps.climate.domain.climate_config import ClimateConfig, ClimateConfigRecord
from apps.climate.domain.control import ClimateControl
from apps.climate.domain.optimal_start import OptimalStartSettings
from apps.climate.domain.room_intent import GuestsRecord
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
    guests_end_hour=14,
)
GUESTS_LEAVE = datetime(2026, 11, 5, 11, 0, tzinfo=UTC)
GUARD_FIELDS = (
    'second_floor_sensor',
    'second_floor_guard_zone',
    'second_floor_guard_threshold',
    'second_floor_guard_hysteresis',
    'second_floor_guard_live',
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
        guests_end_hour=12,
    )


@pytest.fixture
def f_guests_settings() -> ClimateSettings:
    return ClimateSettingsFactory.create(guests_until=GUESTS_LEAVE, guests_end_hour=12)


def test_sensors_name_the_outdoor_sensor_and_not_the_old_second_floor_sensor(f_sensors: list[Device]) -> None:
    assert ClimateSettingsRepository.sensors() == ClimateSensors(outdoor_slug=f_sensors[0].slug)


def test_without_climate_settings_no_sensor_is_named() -> None:
    assert ClimateSettingsRepository.sensors() == ClimateSensors(outdoor_slug=None)


@pytest.mark.usefixtures('tuned_settings')
def test_control_reads_regulation_and_optimal_start_and_no_longer_the_second_floor_guard() -> None:
    assert ClimateSettingsRepository.control() == ClimateControl(
        sensors=ClimateSensors(outdoor_slug='outdoor'),
        regulation=AirRegulationSettings(
            start_offset=Decimal('0.25'),
            initial_early_stop_offset=Decimal('0.40'),
            min_band=Decimal('0.15'),
        ),
        optimal_start=OptimalStartSettings(lead_cap=timedelta(minutes=210), mornings_needed=5),
    )


def test_control_without_climate_settings_takes_defaults() -> None:
    assert ClimateSettingsRepository.control() == ClimateControl(
        sensors=ClimateSensors(outdoor_slug=None),
        regulation=AirRegulationSettings(
            start_offset=Decimal('0.3'),
            initial_early_stop_offset=Decimal('0.3'),
            min_band=Decimal('0.2'),
        ),
        optimal_start=OptimalStartSettings(lead_cap=timedelta(hours=4), mornings_needed=3),
    )


def test_config_record_without_climate_settings_takes_the_defaults() -> None:
    assert ClimateSettingsRepository.config_record() == ClimateConfigRecord(id=None, config=DEFAULT_CONFIG)


def test_config_record_reads_the_stored_settings(f_guarded_settings: ClimateSettings) -> None:
    assert ClimateSettingsRepository.config_record() == ClimateConfigRecord(
        id=f_guarded_settings.pk,
        config=replace(DEFAULT_CONFIG, learned_mornings_needed=5, air_min_band=Decimal('0.25'), guests_end_hour=12),
    )


def test_saved_config_writes_the_named_fields_and_leaves_the_old_guard_columns_alone(
    f_guarded_settings: ClimateSettings,
) -> None:
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


def test_saved_config_without_stored_settings_creates_them_with_the_old_guard_off() -> None:
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


@pytest.mark.parametrize('field', GUARD_FIELDS)
def test_saved_config_refuses_to_write_an_old_guard_column(field: str) -> None:
    with pytest.raises(KeyError):
        ClimateSettingsRepository.save_config(DEFAULT_CONFIG, (field,))


def test_without_climate_settings_no_guests_stay_and_they_would_leave_at_two() -> None:
    assert ClimateSettingsRepository.guests() == GuestsRecord(until=None, end_hour=14)


@pytest.mark.usefixtures('f_guests_settings')
def test_guests_read_their_end_and_the_hour_they_leave() -> None:
    assert ClimateSettingsRepository.guests() == GuestsRecord(until=GUESTS_LEAVE, end_hour=12)


@pytest.mark.parametrize('until', [GUESTS_LEAVE + timedelta(days=1), None], ids=['set', 'cleared'])
def test_saved_guests_round_trip_and_leave_the_other_settings_alone(
    f_guests_settings: ClimateSettings,
    until: datetime | None,
) -> None:
    ClimateSettingsRepository.save_guests(until)

    stored = ClimateSettings.objects.get(pk=f_guests_settings.pk)
    assert (ClimateSettingsRepository.guests(), stored.learned_mornings_needed, ClimateSettings.objects.count()) == (
        GuestsRecord(until=until, end_hour=12),
        3,
        1,
    )


def test_saved_guests_without_stored_settings_create_them() -> None:
    ClimateSettingsRepository.save_guests(GUESTS_LEAVE)

    stored = ClimateSettings.objects.get()
    assert (stored.guests_until, stored.guests_end_hour, stored.air_start_offset) == (GUESTS_LEAVE, 14, Decimal('0.30'))


def test_guests_for_update_take_the_write_lock_before_they_are_read(f_guests_settings: ClimateSettings) -> None:
    with CaptureQueriesContext(connection) as queries:
        guests = ClimateSettingsRepository.guests_for_update()

    assert (guests.until, [query['sql'].split(' ', 1)[0] for query in queries.captured_queries]) == (
        f_guests_settings.guests_until,
        ['UPDATE', 'SELECT'],
    )


def test_saved_config_writes_the_guests_end_hour(f_guarded_settings: ClimateSettings) -> None:
    ClimateSettingsRepository.save_config(replace(DEFAULT_CONFIG, guests_end_hour=11), ('guests_end_hour',))

    assert ClimateSettings.objects.get(pk=f_guarded_settings.pk).guests_end_hour == 11


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
