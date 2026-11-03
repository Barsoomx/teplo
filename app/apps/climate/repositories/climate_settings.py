from datetime import timedelta

from apps.climate.domain.air_policy import AirRegulationSettings
from apps.climate.domain.control import ClimateControl
from apps.climate.domain.optimal_start import OptimalStartSettings
from apps.climate.domain.second_floor_guard import SecondFloorGuardSettings
from apps.climate.domain.sensors import ClimateSensors
from apps.climate.models import ClimateSettings, Zone
from apps.devices.models import Device


class ClimateSettingsRepository:
    @staticmethod
    def sensors() -> ClimateSensors:
        climate_settings = (
            ClimateSettings.objects.select_related('outdoor_sensor', 'second_floor_sensor').order_by('pk').first()
        )
        if climate_settings is None:
            return ClimateSensors(outdoor_slug=None, second_floor_slug=None)

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
            second_floor_guard=SecondFloorGuardSettings(
                zone_slug=_zone_slug(climate_settings.second_floor_guard_zone),
                threshold=climate_settings.second_floor_guard_threshold,
                hysteresis=climate_settings.second_floor_guard_hysteresis,
                live=climate_settings.second_floor_guard_live,
            ),
        )


def _stored_or_default() -> ClimateSettings:
    stored = (
        ClimateSettings.objects.select_related('outdoor_sensor', 'second_floor_sensor', 'second_floor_guard_zone')
        .order_by('pk')
        .first()
    )
    return ClimateSettings() if stored is None else stored


def _sensors(climate_settings: ClimateSettings) -> ClimateSensors:
    return ClimateSensors(
        outdoor_slug=_slug(climate_settings.outdoor_sensor),
        second_floor_slug=_slug(climate_settings.second_floor_sensor),
    )


def _slug(device: Device | None) -> str | None:
    return None if device is None else device.slug


def _zone_slug(zone: Zone | None) -> str | None:
    return None if zone is None else zone.slug
