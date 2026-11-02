from django.db import models

from apps.devices.domain.kinds import DeviceKind

TEMPERATURE_SENSORS = {'kind': DeviceKind.TEMPERATURE_SENSOR.value}


class ClimateSettings(models.Model):
    outdoor_sensor = models.ForeignKey(
        'devices.Device',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        limit_choices_to=TEMPERATURE_SENSORS,
    )
    second_floor_sensor = models.ForeignKey(
        'devices.Device',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        limit_choices_to=TEMPERATURE_SENSORS,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'climate settings'

    def __str__(self) -> str:
        return 'Climate settings'
