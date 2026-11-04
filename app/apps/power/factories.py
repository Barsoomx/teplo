from datetime import timedelta

import factory
from django.utils import timezone

from apps.climate.domain.heat_sources import HeatSourceKind
from apps.climate.factories import HeatSourceFactory, ZoneFactory
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.factories import DeviceFactory
from apps.power.domain.loads import LoadKind
from apps.power.models import Load, LoadSettings, Outage


class OutageFactory(factory.django.DjangoModelFactory):
    began_at = factory.LazyAttribute(lambda outage: outage.ended_at - timedelta(minutes=9))
    ended_at = factory.LazyFunction(timezone.now)

    class Meta:
        model = Outage


class LoadFactory(factory.django.DjangoModelFactory):
    slug = factory.Sequence(lambda n: f'load-{n}')
    name = factory.Sequence(lambda n: f'Load {n}')
    priority = factory.Sequence(lambda n: n + 1)

    class Meta:
        model = Load


class PlugLoadFactory(LoadFactory):
    kind = LoadKind.PLUG
    device = factory.SubFactory(DeviceFactory, kind=DeviceKind.PLUG, driver=DeviceDriver.TUYA_SWITCHABLE_PLUG)


class ZoneLoadFactory(LoadFactory):
    kind = LoadKind.ZONE
    zone = factory.SubFactory(ZoneFactory)


class SourceLoadFactory(LoadFactory):
    kind = LoadKind.SOURCE
    heat_source = factory.SubFactory(
        HeatSourceFactory,
        kind=HeatSourceKind.HEATER,
        device=factory.SubFactory(DeviceFactory, kind=DeviceKind.CLIMATE_UNIT, driver=DeviceDriver.TUYA_HEATER),
    )


class LoadSettingsFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = LoadSettings
