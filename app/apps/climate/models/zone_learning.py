from django.db import models

from apps.climate.models.climate_settings import EARLY_STOP_OFFSET_VALIDATORS


class ZoneLearning(models.Model):
    zone = models.OneToOneField('climate.Zone', on_delete=models.CASCADE, related_name='learning')
    early_stop_offset = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        null=True,
        blank=True,
        validators=EARLY_STOP_OFFSET_VALIDATORS,
    )
    dead_time = models.DurationField(null=True, blank=True)
    heat_up_rates = models.JSONField(default=dict, blank=True)
    mornings = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('zone',)
        verbose_name_plural = 'zone learning'

    def __str__(self) -> str:
        return str(self.zone)
