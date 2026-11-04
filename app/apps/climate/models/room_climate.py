from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy

from apps.climate.domain.modes import CommandSource
from apps.climate.domain.room_climate import (
    BACKUP_MARGIN,
    VACANT_AFTER_MINUTES_DEFAULT,
    VACANT_AFTER_MINUTES_MAX,
    VACANT_AFTER_MINUTES_MIN,
)
from apps.climate.models.climate_settings import OFFSET_VALIDATORS, TEMPERATURE_SENSORS
from apps.climate.models.zone import SETPOINT_VALIDATORS

# SQLite compares these columns as floats: half a tenth of slack keeps a threshold exactly 0.5° below from failing.
BACKUP_CHECK_MARGIN = BACKUP_MARGIN - Decimal('0.05')
SETBACK_ABOVE_COMFORT = gettext_lazy('The night target must not be above the comfort target')
BACKUP_NOT_BELOW_SETBACK = gettext_lazy(
    'Backup heat must turn on at least 0.5° below the night target, or it fights the night target',
)
FOLLOWS_OCCUPANCY_HELP = gettext_lazy(
    'While on and the room’s occupancy is known, the heat sources teplo commands hold the comfort target while '
    'someone is here and the night target once nobody has been here for the minutes below, inside and outside the '
    'comfort windows; an override and staying guests still win, and a room with a heated floor never follows '
    'occupancy. While off, occupancy is only shown and journaled',
)
VACANT_AFTER_HELP = gettext_lazy('Minutes without anyone before the room counts as empty')


class RoomClimate(models.Model):
    room = models.OneToOneField('house.Room', on_delete=models.CASCADE, related_name='climate')
    air_sensor = models.ForeignKey(
        'devices.Device',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='air_rooms',
        limit_choices_to=TEMPERATURE_SENSORS,
    )
    comfort_target = models.DecimalField(
        max_digits=3,
        decimal_places=1,
        default=Decimal('22.0'),
        validators=SETPOINT_VALIDATORS,
    )
    setback_target = models.DecimalField(
        max_digits=3,
        decimal_places=1,
        default=Decimal('19.5'),
        validators=SETPOINT_VALIDATORS,
    )
    backup_zone = models.ForeignKey(
        'climate.Zone',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    backup_below = models.DecimalField(
        max_digits=3,
        decimal_places=1,
        default=Decimal('19.0'),
        validators=SETPOINT_VALIDATORS,
    )
    backup_hysteresis = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=Decimal('0.30'),
        validators=OFFSET_VALIDATORS,
    )
    backup_live = models.BooleanField(default=False)
    guest_room = models.BooleanField(default=False, db_default=False)
    override_target = models.DecimalField(
        max_digits=3,
        decimal_places=1,
        null=True,
        blank=True,
        validators=SETPOINT_VALIDATORS,
    )
    override_until = models.DateTimeField(null=True, blank=True)
    override_source = models.CharField(
        max_length=16,
        choices=[(source.value, source.value) for source in CommandSource],
        blank=True,
        default='',
        db_default='',
    )
    follows_occupancy = models.BooleanField(default=False, db_default=False, help_text=FOLLOWS_OCCUPANCY_HELP)
    vacant_after_minutes = models.PositiveSmallIntegerField(
        default=VACANT_AFTER_MINUTES_DEFAULT,
        db_default=VACANT_AFTER_MINUTES_DEFAULT,
        validators=[MinValueValidator(VACANT_AFTER_MINUTES_MIN), MaxValueValidator(VACANT_AFTER_MINUTES_MAX)],
        help_text=VACANT_AFTER_HELP,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('room__floor__level', 'room__position', 'room__name')
        constraints = [
            models.CheckConstraint(
                condition=models.Q(setback_target__lte=models.F('comfort_target')),
                name='room_climate_setback_not_above_comfort',
                violation_error_message=SETBACK_ABOVE_COMFORT,
            ),
            models.CheckConstraint(
                condition=models.Q(backup_zone__isnull=True)
                | models.Q(backup_below__lte=models.F('setback_target') - BACKUP_CHECK_MARGIN),
                name='room_climate_backup_below_setback',
                violation_error_message=BACKUP_NOT_BELOW_SETBACK,
            ),
        ]

    def __str__(self) -> str:
        return str(self.room)
