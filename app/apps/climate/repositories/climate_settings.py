from apps.climate.domain.sensors import ClimateSensors
from apps.climate.models import ClimateSettings
from apps.devices.models import Device


class ClimateSettingsRepository:
    @staticmethod
    def sensors() -> ClimateSensors:
        climate_settings = (
            ClimateSettings.objects.select_related('outdoor_sensor', 'second_floor_sensor').order_by('pk').first()
        )
        if climate_settings is None:
            return ClimateSensors(outdoor_slug=None, second_floor_slug=None)

        return ClimateSensors(
            outdoor_slug=_slug(climate_settings.outdoor_sensor),
            second_floor_slug=_slug(climate_settings.second_floor_sensor),
        )


def _slug(device: Device | None) -> str | None:
    return None if device is None else device.slug
