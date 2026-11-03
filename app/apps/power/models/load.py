from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.devices.domain.kinds import SWITCHABLE_PLUG_DRIVERS, DeviceKind
from apps.power.domain.load_config import AMPS_MAX, AMPS_MIN, PRIORITY_MIN
from apps.power.domain.loads import LoadKind, LoadPhase, ShedReason

AMPS_VALIDATORS = [MinValueValidator(AMPS_MIN), MaxValueValidator(AMPS_MAX)]
PHASE_CHOICES = [(phase.value, phase.value) for phase in LoadPhase]
SHEDDABLE_PLUGS = {
    'kind': DeviceKind.PLUG.value,
    'monitor_only': False,
    'driver__in': [driver.value for driver in SWITCHABLE_PLUG_DRIVERS],
}


class Load(models.Model):
    slug = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=16, choices=[(kind.value, kind.value) for kind in LoadKind])
    zone = models.OneToOneField(
        'climate.Zone',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='load',
    )
    device = models.OneToOneField(
        'devices.Device',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='load',
        limit_choices_to=SHEDDABLE_PLUGS,
    )
    priority = models.PositiveIntegerField(unique=True, validators=[MinValueValidator(PRIORITY_MIN)])
    phase = models.CharField(max_length=16, choices=PHASE_CHOICES, default=LoadPhase.UNKNOWN.value)
    nominal_amps = models.DecimalField(
        max_digits=4,
        decimal_places=1,
        null=True,
        blank=True,
        validators=AMPS_VALIDATORS,
    )
    learned_phase = models.CharField(
        max_length=16,
        choices=PHASE_CHOICES,
        default=LoadPhase.UNKNOWN.value,
        editable=False,
    )
    learned_amps = models.DecimalField(max_digits=4, decimal_places=1, null=True, editable=False)
    learning_samples = models.PositiveIntegerField(default=0, editable=False)
    live = models.BooleanField(default=False)
    shed_at = models.DateTimeField(null=True, editable=False)
    shed_reason = models.CharField(
        max_length=16,
        choices=[(reason.value, reason.value) for reason in ShedReason],
        blank=True,
        editable=False,
    )
    shed_phase = models.CharField(max_length=16, choices=PHASE_CHOICES, blank=True, editable=False)
    shed_was_on = models.BooleanField(null=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('priority',)
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(kind=LoadKind.ZONE.value, zone__isnull=False, device__isnull=True)
                    | models.Q(kind=LoadKind.PLUG.value, zone__isnull=True, device__isnull=False)
                ),
                name='load_controls_its_zone_or_its_plug',
            ),
            models.CheckConstraint(condition=models.Q(priority__gte=1), name='load_priority_positive'),
        ]

    def __str__(self) -> str:
        return self.name
