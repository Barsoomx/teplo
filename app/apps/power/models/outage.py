from django.db import models

from apps.power.domain.outage import OutageCause


class Outage(models.Model):
    began_at = models.DateTimeField()
    ended_at = models.DateTimeField()
    cause = models.CharField(
        max_length=16,
        choices=[(cause.value, cause.value) for cause in OutageCause],
        default=OutageCause.UNMARKED.value,
    )
    cause_marked_at = models.DateTimeField(null=True, blank=True)
    boot_id = models.CharField(max_length=36, null=True, unique=True, editable=False)

    class Meta:
        ordering = ('-began_at', '-id')

    def __str__(self) -> str:
        return f'{self.began_at:%Y-%m-%d %H:%M}–{self.ended_at:%H:%M} {self.cause}'
