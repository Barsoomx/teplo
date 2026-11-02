from datetime import timedelta

import factory
from django.utils import timezone

from apps.power.models import Outage


class OutageFactory(factory.django.DjangoModelFactory):
    began_at = factory.LazyAttribute(lambda outage: outage.ended_at - timedelta(minutes=9))
    ended_at = factory.LazyFunction(timezone.now)

    class Meta:
        model = Outage
