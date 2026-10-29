import factory
from django.utils import timezone

from apps.telemetry.models import JournalEntry


class JournalEntryFactory(factory.django.DjangoModelFactory):
    kind = 'zone_boosted'
    subject = 'kitchen'
    payload = factory.LazyFunction(dict)
    created_at = factory.LazyFunction(timezone.now)

    class Meta:
        model = JournalEntry
