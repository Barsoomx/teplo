from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.power.domain.load_config import SECONDS_MAX, SECONDS_MIN
from apps.power.models.load import AMPS_VALIDATORS

SECONDS_VALIDATORS = [MinValueValidator(SECONDS_MIN), MaxValueValidator(SECONDS_MAX)]


class LoadSettings(models.Model):
    load_management_live = models.BooleanField(default=False)
    start_ceiling_amps = models.DecimalField(
        max_digits=4,
        decimal_places=1,
        default=Decimal('20.0'),
        validators=AMPS_VALIDATORS,
    )
    shed_amps = models.DecimalField(
        max_digits=4,
        decimal_places=1,
        default=Decimal('24.0'),
        validators=AMPS_VALIDATORS,
    )
    shed_hold_seconds = models.PositiveIntegerField(default=3, validators=SECONDS_VALIDATORS)
    restore_amps = models.DecimalField(
        max_digits=4,
        decimal_places=1,
        default=Decimal('18.0'),
        validators=AMPS_VALIDATORS,
    )
    restore_hold_seconds = models.PositiveIntegerField(default=120, validators=SECONDS_VALIDATORS)
    shed_interval_seconds = models.PositiveIntegerField(default=1, validators=SECONDS_VALIDATORS)
    restore_interval_seconds = models.PositiveIntegerField(default=60, validators=SECONDS_VALIDATORS)
    start_interval_seconds = models.PositiveIntegerField(default=30, validators=SECONDS_VALIDATORS)
    meter_lost_after_seconds = models.PositiveIntegerField(default=60, validators=SECONDS_VALIDATORS)
    default_load_amps = models.DecimalField(
        max_digits=4,
        decimal_places=1,
        default=Decimal('6.0'),
        validators=AMPS_VALIDATORS,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'load settings'
        constraints = [
            models.CheckConstraint(
                condition=models.Q(restore_amps__lte=models.F('start_ceiling_amps')),
                name='load_settings_restore_at_or_below_start_ceiling',
            ),
            models.CheckConstraint(
                condition=models.Q(start_ceiling_amps__lt=models.F('shed_amps')),
                name='load_settings_start_ceiling_below_shed',
            ),
        ]

    def __str__(self) -> str:
        return 'Load settings'
