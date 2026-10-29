from django.db import models


class Sample(models.Model):
    metric = models.CharField(max_length=64)
    subject = models.CharField(max_length=64)
    value = models.FloatField()
    recorded_at = models.DateTimeField()

    class Meta:
        indexes = [models.Index(fields=('metric', 'subject', 'recorded_at'), name='sample_series_idx')]

    def __str__(self) -> str:
        return f'{self.metric} {self.subject} {self.value}'
