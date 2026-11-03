from collections.abc import Collection
from dataclasses import asdict
from datetime import datetime, timedelta

from apps.climate.domain.air_policy import AirRegulationSettings
from apps.climate.domain.climate_config import ClimateConfig, ClimateConfigRecord
from apps.climate.domain.control import ClimateControl
from apps.climate.domain.optimal_start import OptimalStartSettings
from apps.climate.domain.room_intent import GuestsRecord
from apps.climate.domain.sensors import ClimateSensors
from apps.climate.models import ClimateSettings
from apps.core.repositories.write_lock import lock_for_update
from apps.devices.models import Device


class ClimateSettingsRepository:
    @staticmethod
    def sensors() -> ClimateSensors:
        climate_settings = ClimateSettings.objects.select_related('outdoor_sensor').order_by('pk').first()
        if climate_settings is None:
            return ClimateSensors(outdoor_slug=None)

        return _sensors(climate_settings)

    @staticmethod
    def control() -> ClimateControl:
        climate_settings = _stored_or_default()
        return ClimateControl(
            sensors=_sensors(climate_settings),
            regulation=AirRegulationSettings(
                start_offset=climate_settings.air_start_offset,
                initial_early_stop_offset=climate_settings.initial_early_stop_offset,
                min_band=climate_settings.air_min_band,
            ),
            optimal_start=OptimalStartSettings(
                lead_cap=timedelta(minutes=climate_settings.preheat_lead_cap_minutes),
                mornings_needed=climate_settings.learned_mornings_needed,
            ),
        )

    @staticmethod
    def config_record() -> ClimateConfigRecord:
        stored = ClimateSettings.objects.order_by('pk').first()
        climate_settings = ClimateSettings() if stored is None else stored
        return ClimateConfigRecord(
            id=None if stored is None else stored.pk,
            config=ClimateConfig(
                air_start_offset=climate_settings.air_start_offset,
                initial_early_stop_offset=climate_settings.initial_early_stop_offset,
                air_min_band=climate_settings.air_min_band,
                learned_mornings_needed=climate_settings.learned_mornings_needed,
                preheat_lead_cap_minutes=climate_settings.preheat_lead_cap_minutes,
                guests_end_hour=climate_settings.guests_end_hour,
            ),
        )

    @staticmethod
    def config_record_for_update() -> ClimateConfigRecord:
        lock_for_update(ClimateSettings.objects.all())
        return ClimateSettingsRepository.config_record()

    @staticmethod
    def save_config(config: ClimateConfig, fields: Collection[str]) -> None:
        values = asdict(config)
        changes = {field: values[field] for field in fields}
        if not changes:
            return

        stored = ClimateSettings.objects.order_by('pk').first()
        if stored is None:
            ClimateSettings.objects.create(**changes)
            return

        ClimateSettings.objects.filter(pk=stored.pk).update(**changes)

    @staticmethod
    def guests() -> GuestsRecord:
        stored = ClimateSettings.objects.order_by('pk').first()
        climate_settings = ClimateSettings() if stored is None else stored
        return GuestsRecord(until=climate_settings.guests_until, end_hour=climate_settings.guests_end_hour)

    @staticmethod
    def guests_for_update() -> GuestsRecord:
        lock_for_update(ClimateSettings.objects.all())
        return ClimateSettingsRepository.guests()

    @staticmethod
    def save_guests(until: datetime | None) -> None:
        stored = ClimateSettings.objects.order_by('pk').first()
        if stored is None:
            ClimateSettings.objects.create(guests_until=until)
            return

        ClimateSettings.objects.filter(pk=stored.pk).update(guests_until=until)


def _stored_or_default() -> ClimateSettings:
    stored = ClimateSettings.objects.select_related('outdoor_sensor').order_by('pk').first()
    return ClimateSettings() if stored is None else stored


def _sensors(climate_settings: ClimateSettings) -> ClimateSensors:
    return ClimateSensors(outdoor_slug=_slug(climate_settings.outdoor_sensor))


def _slug(device: Device | None) -> str | None:
    return None if device is None else device.slug
