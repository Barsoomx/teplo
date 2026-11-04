from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy

from apps.climate.domain.climate_config import (
    GUESTS_END_HOUR,
    GUESTS_END_HOUR_MAX,
    MIN_BAND_MAX,
    MIN_BAND_MIN,
    MORNINGS_NEEDED_MIN,
    OFFSET_MAX,
    OFFSET_MIN,
    PREHEAT_LEAD_CAP_MAX_MINUTES,
)
from apps.climate.domain.learning import EARLY_STOP_OFFSET_MAX, EARLY_STOP_OFFSET_MIN
from apps.climate.models.zone import SETPOINT_VALIDATORS
from apps.devices.domain.kinds import DeviceKind

TEMPERATURE_SENSORS = {'kind': DeviceKind.TEMPERATURE_SENSOR.value}
OFFSET_VALIDATORS = [MinValueValidator(OFFSET_MIN), MaxValueValidator(OFFSET_MAX)]
EARLY_STOP_OFFSET_VALIDATORS = [MinValueValidator(EARLY_STOP_OFFSET_MIN), MaxValueValidator(EARLY_STOP_OFFSET_MAX)]
MIN_BAND_VALIDATORS = [MinValueValidator(MIN_BAND_MIN), MaxValueValidator(MIN_BAND_MAX)]
START_OFFSET_BELOW_MIN_BAND = gettext_lazy('Must not be below the air min band, or heating stops above the target')
MANUAL_HOLD_HELP = gettext_lazy(
    'While on, a change made on a wall thermostat or in its app holds that floor for 2 hours; while off, it is '
    'only journaled and teplo reverts it at the zone’s next allowed write',
)


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
    preheat_lead_cap_minutes = models.PositiveIntegerField(
        default=240,
        validators=[MaxValueValidator(PREHEAT_LEAD_CAP_MAX_MINUTES)],
    )
    learned_mornings_needed = models.PositiveSmallIntegerField(
        default=3,
        validators=[MinValueValidator(MORNINGS_NEEDED_MIN)],
    )
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
    guests_until = models.DateTimeField(null=True, blank=True)
    guests_end_hour = models.PositiveSmallIntegerField(
        default=GUESTS_END_HOUR,
        db_default=GUESTS_END_HOUR,
        validators=[MaxValueValidator(GUESTS_END_HOUR_MAX)],
    )
    manual_hold_live = models.BooleanField(default=False, db_default=False, help_text=MANUAL_HOLD_HELP)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'climate settings'
        constraints = [
            models.CheckConstraint(
                condition=models.Q(air_start_offset__gte=models.F('air_min_band')),
                name='climate_settings_start_offset_keeps_min_band',
                violation_error_message=START_OFFSET_BELOW_MIN_BAND,
            ),
        ]

    def __str__(self) -> str:
        return 'Climate settings'
