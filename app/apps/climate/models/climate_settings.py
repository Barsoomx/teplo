from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.climate.domain.learning import EARLY_STOP_OFFSET_MAX, EARLY_STOP_OFFSET_MIN
from apps.climate.models.zone import SETPOINT_VALIDATORS
from apps.devices.domain.kinds import DeviceKind

TEMPERATURE_SENSORS = {'kind': DeviceKind.TEMPERATURE_SENSOR.value}
OFFSET_VALIDATORS = [MinValueValidator(Decimal('0.00')), MaxValueValidator(Decimal('2.00'))]
EARLY_STOP_OFFSET_VALIDATORS = [MinValueValidator(EARLY_STOP_OFFSET_MIN), MaxValueValidator(EARLY_STOP_OFFSET_MAX)]
MIN_BAND_VALIDATORS = [MinValueValidator(Decimal('0.10')), MaxValueValidator(Decimal('1.00'))]


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
    air_start_offset = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=Decimal('0.30'),
        validators=OFFSET_VALIDATORS,
    )
    initial_early_stop_offset = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=Decimal('0.30'),
        validators=EARLY_STOP_OFFSET_VALIDATORS,
    )
    air_min_band = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=Decimal('0.20'),
        validators=MIN_BAND_VALIDATORS,
    )
    preheat_lead_cap_minutes = models.PositiveIntegerField(default=240, validators=[MaxValueValidator(480)])
    learned_mornings_needed = models.PositiveSmallIntegerField(default=3, validators=[MinValueValidator(1)])
    second_floor_guard_zone = models.ForeignKey(
        'climate.Zone',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    second_floor_guard_threshold = models.DecimalField(
        max_digits=3,
        decimal_places=1,
        default=Decimal('19.0'),
        validators=SETPOINT_VALIDATORS,
    )
    second_floor_guard_hysteresis = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=Decimal('0.30'),
        validators=OFFSET_VALIDATORS,
    )
    second_floor_guard_live = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'climate settings'

    def __str__(self) -> str:
        return 'Climate settings'
