import factory

from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.models import Device


class DeviceFactory(factory.django.DjangoModelFactory):
    slug = factory.Sequence(lambda n: f'device-{n}')
    name = factory.Sequence(lambda n: f'Device {n}')
    kind = DeviceKind.THERMOSTAT
    driver = DeviceDriver.TUYA_THERMOSTAT

    class Meta:
        model = Device
