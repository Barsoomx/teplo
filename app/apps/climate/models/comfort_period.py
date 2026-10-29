from django.db import models

WEEKDAYS = [
    (0, 'Monday'),
    (1, 'Tuesday'),
    (2, 'Wednesday'),
    (3, 'Thursday'),
    (4, 'Friday'),
    (5, 'Saturday'),
    (6, 'Sunday'),
]


class ComfortPeriod(models.Model):
    zone = models.ForeignKey('climate.Zone', on_delete=models.CASCADE, related_name='comfort_periods')
    weekday = models.PositiveSmallIntegerField(choices=WEEKDAYS)
    start = models.TimeField()
    end = models.TimeField()

    class Meta:
        ordering = ('zone', 'weekday', 'start')
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end__gt=models.F('start')),
                name='comfort_period_end_after_start',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.get_weekday_display()} {self.start:%H:%M}-{self.end:%H:%M}'
