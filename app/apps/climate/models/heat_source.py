from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy

from apps.climate.domain.heat_sources import (
    MAX_SETPOINT_DEFAULT,
    MAX_SETPOINT_MAX,
    MAX_SETPOINT_MIN,
    MIN_OUTDOOR_MAX,
    MIN_OUTDOOR_MIN,
    MIN_WRITE_MINUTES_DEFAULT,
    MIN_WRITE_MINUTES_MAX,
    MIN_WRITE_MINUTES_MIN,
    RATED_WATTS_MAX,
    RATED_WATTS_MIN,
    SETPOINT_BIAS_DEFAULT,
    SETPOINT_BIAS_MAX,
    SETPOINT_BIAS_MIN,
    HeatSourceKind,
)
from apps.devices.domain.kinds import DeviceKind

DEVICE_UNLESS_FIREPLACE = gettext_lazy('A fireplace has no device, and any other heat source needs its device')
DEVICE_HELP = gettext_lazy(
    'Empty for a fireplace, which teplo only observes; a heater or an air conditioner needs its climate unit',
)
LIVE_HELP = gettext_lazy(
    'The source’s own gate: for a fireplace, a fire the detector sees withholds learning as a mark does; '
    'for a heater, its commands reach the device once load management and the source’s load are live too',
)


class HeatSource(models.Model):
    slug = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=100, unique=True)
    room = models.ForeignKey('house.Room', on_delete=models.PROTECT, related_name='heat_sources')
    kind = models.CharField(max_length=16, choices=[(kind.value, kind.value) for kind in HeatSourceKind])
    device = models.OneToOneField(
        'devices.Device',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='heat_source',
        limit_choices_to={'kind': DeviceKind.CLIMATE_UNIT.value},
        help_text=DEVICE_HELP,
    )
    priority = models.PositiveSmallIntegerField(default=1)
    live = models.BooleanField(default=False, help_text=LIVE_HELP)
    lit_until = models.DateTimeField(null=True, blank=True)
    setpoint_bias = models.DecimalField(
        max_digits=3,
        decimal_places=1,
        default=SETPOINT_BIAS_DEFAULT,
        db_default=SETPOINT_BIAS_DEFAULT,
        validators=[MinValueValidator(SETPOINT_BIAS_MIN), MaxValueValidator(SETPOINT_BIAS_MAX)],
    )
    max_setpoint = models.DecimalField(
        max_digits=3,
        decimal_places=1,
        default=MAX_SETPOINT_DEFAULT,
        db_default=MAX_SETPOINT_DEFAULT,
        validators=[MinValueValidator(MAX_SETPOINT_MIN), MaxValueValidator(MAX_SETPOINT_MAX)],
    )
    min_outdoor = models.DecimalField(
        max_digits=3,
        decimal_places=1,
        null=True,
        blank=True,
        validators=[MinValueValidator(MIN_OUTDOOR_MIN), MaxValueValidator(MIN_OUTDOOR_MAX)],
    )
    min_write_minutes = models.PositiveSmallIntegerField(
        default=MIN_WRITE_MINUTES_DEFAULT,
        db_default=MIN_WRITE_MINUTES_DEFAULT,
        validators=[MinValueValidator(MIN_WRITE_MINUTES_MIN), MaxValueValidator(MIN_WRITE_MINUTES_MAX)],
    )
    rated_watts = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(RATED_WATTS_MIN), MaxValueValidator(RATED_WATTS_MAX)],
    )
    manual_until = models.DateTimeField(null=True, blank=True)
    last_command_at = models.DateTimeField(null=True, blank=True)
    last_dry_run_at = models.DateTimeField(null=True, blank=True)
    commanded_power = models.BooleanField(null=True, blank=True)
    commanded_setpoint = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('room__floor__level', 'room__position', 'priority')
        constraints = [
            models.UniqueConstraint(fields=('room', 'priority'), name='heat_source_priority_unique_in_room'),
            models.CheckConstraint(
                condition=(
                    models.Q(kind=HeatSourceKind.FIREPLACE.value, device__isnull=True)
                    | (~models.Q(kind=HeatSourceKind.FIREPLACE.value) & models.Q(device__isnull=False))
                ),
                name='heat_source_device_unless_fireplace',
                violation_error_message=DEVICE_UNLESS_FIREPLACE,
            ),
        ]

    def __str__(self) -> str:
        return self.name
