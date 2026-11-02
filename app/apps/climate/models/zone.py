from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.climate.domain.modes import ZoneKind
from apps.devices.domain.kinds import DeviceKind

SETPOINT_VALIDATORS = [MinValueValidator(Decimal('5.0')), MaxValueValidator(Decimal('40.0'))]


class Zone(models.Model):
    slug = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=100)
    kind = models.CharField(
        max_length=16,
        choices=[(kind.value, kind.value) for kind in ZoneKind],
        default=ZoneKind.AIR.value,
    )
    thermostat = models.ForeignKey('devices.Device', on_delete=models.PROTECT, related_name='zones')
    air_sensor = models.ForeignKey(
        'devices.Device',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='air_zones',
        limit_choices_to={'kind': DeviceKind.TEMPERATURE_SENSOR.value},
    )
    heating_enabled = models.BooleanField(default=True)
    live_control = models.BooleanField(default=False)
    air_control_live = models.BooleanField(default=False)
    comfort_floor_setpoint = models.DecimalField(
        max_digits=3,
        decimal_places=1,
        default=Decimal('28.0'),
        validators=SETPOINT_VALIDATORS,
    )
    night_floor_setpoint = models.DecimalField(
        max_digits=3,
        decimal_places=1,
        default=Decimal('22.0'),
        validators=SETPOINT_VALIDATORS,
    )
    boost_floor_setpoint = models.DecimalField(
        max_digits=3,
        decimal_places=1,
        default=Decimal('30.0'),
        validators=SETPOINT_VALIDATORS,
    )
    idle_floor_setpoint = models.DecimalField(
        max_digits=3,
        decimal_places=1,
        default=Decimal('18.0'),
        validators=SETPOINT_VALIDATORS,
    )
    floor_limit_setpoint = models.DecimalField(
        max_digits=3,
        decimal_places=1,
        default=Decimal('30.0'),
        validators=SETPOINT_VALIDATORS,
    )
    comfort_air_target = models.DecimalField(
        max_digits=3,
        decimal_places=1,
        default=Decimal('22.0'),
        validators=SETPOINT_VALIDATORS,
    )
    night_air_target = models.DecimalField(
        max_digits=3,
        decimal_places=1,
        default=Decimal('19.5'),
        validators=SETPOINT_VALIDATORS,
    )
    preheat_lead_minutes = models.PositiveIntegerField(default=180, validators=[MaxValueValidator(240)])
    boost_until = models.DateTimeField(null=True, blank=True)
    last_command_at = models.DateTimeField(null=True, blank=True)
    last_dry_run_at = models.DateTimeField(null=True, blank=True)
    last_air_dry_run_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('slug',)

    def __str__(self) -> str:
        return self.name
