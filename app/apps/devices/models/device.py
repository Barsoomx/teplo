from django.db import models

from apps.devices.domain.kinds import COMMANDING_DRIVERS, DeviceDriver, DeviceKind


class Device(models.Model):
    slug = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=32, choices=[(kind.value, kind.value) for kind in DeviceKind])
    driver = models.CharField(max_length=32, choices=[(driver.value, driver.value) for driver in DeviceDriver])
    monitor_only = models.BooleanField(default=False)
    room = models.ForeignKey(
        'house.Room',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='devices',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('slug',)
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(monitor_only=False) | ~models.Q(driver__in=[driver.value for driver in COMMANDING_DRIVERS])
                ),
                name='device_monitor_only_never_commanded',
            ),
        ]

    def __str__(self) -> str:
        return self.name
