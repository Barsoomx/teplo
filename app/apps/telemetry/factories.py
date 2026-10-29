import factory
from django.utils import timezone

from apps.telemetry.models import JournalEntry, Sample


class JournalEntryFactory(factory.django.DjangoModelFactory):
    kind = 'zone_boosted'
    subject = 'kitchen'
    payload = factory.LazyFunction(dict)
    created_at = factory.LazyFunction(timezone.now)

    class Meta:
        model = JournalEntry


class SampleFactory(factory.django.DjangoModelFactory):
    metric = 'floor_temperature'
    subject = 'thermostat-kitchen'
    value = 24.5
    recorded_at = factory.LazyFunction(timezone.now)

    class Meta:
        model = Sample
