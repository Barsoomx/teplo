from django.db import models
from django.utils.translation import gettext_lazy

from apps.climate.domain.heat_sources import HeatSourceKind
from apps.devices.domain.kinds import DeviceKind

DEVICE_UNLESS_FIREPLACE = gettext_lazy('A fireplace has no device, and any other heat source needs its device')
DEVICE_HELP = gettext_lazy(
    'Empty for a fireplace, which teplo only observes; a heater or an air conditioner needs its climate unit',
)
LIVE_HELP = gettext_lazy(
    'The source’s own gate: for a fireplace, a fire the detector sees withholds learning as a mark does',
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
