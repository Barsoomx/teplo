from datetime import time

import factory

from apps.climate.domain.modes import ZoneKind
from apps.climate.models import ClimateSettings, ComfortPeriod, RoomClimate, Zone, ZoneLearning
from apps.devices.factories import DeviceFactory
from apps.house.factories import RoomFactory


class ZoneFactory(factory.django.DjangoModelFactory):
    slug = factory.Sequence(lambda n: f'zone-{n}')
    name = factory.Sequence(lambda n: f'Zone {n}')
    kind = ZoneKind.AIR
    thermostat = factory.SubFactory(DeviceFactory)

    class Meta:
        model = Zone


class RoomClimateFactory(factory.django.DjangoModelFactory):
    room = factory.SubFactory(RoomFactory)

    class Meta:
        model = RoomClimate


class ComfortPeriodFactory(factory.django.DjangoModelFactory):
    zone = factory.SubFactory(ZoneFactory)
    weekday = factory.Iterator(range(7))
    start = time(6, 30)
    end = time(23, 30)

    class Meta:
        model = ComfortPeriod


class ClimateSettingsFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ClimateSettings


class ZoneLearningFactory(factory.django.DjangoModelFactory):
    zone = factory.SubFactory(ZoneFactory)

    class Meta:
        model = ZoneLearning
