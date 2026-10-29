from django.core.serializers.json import DjangoJSONEncoder
from django.db import models


class JournalEntry(models.Model):
    kind = models.CharField(max_length=64)
    subject = models.CharField(max_length=64, blank=True)
    payload = models.JSONField(default=dict, encoder=DjangoJSONEncoder)
    created_at = models.DateTimeField()

    class Meta:
        ordering = ('-created_at', '-id')
        indexes = [models.Index(fields=('-created_at',), name='journal_created_idx')]

    def __str__(self) -> str:
        return f'{self.created_at:%Y-%m-%d %H:%M:%S} {self.kind} {self.subject}'
