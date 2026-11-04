from apps.devices.domain.device import DeviceSettings, EsphomeDevice
from apps.devices.domain.errors import DeviceNotFoundError
from apps.devices.domain.kinds import ESPHOME_DRIVERS, DeviceDriver, DeviceKind
from apps.devices.models import Device


class DeviceRepository:
    @staticmethod
    def get(slug: str) -> Device:
        device = Device.objects.filter(slug=slug).first()
        if device is None:
            raise DeviceNotFoundError(slug)

        return device

    @staticmethod
    def thermostat_slugs() -> list[str]:
        return list(Device.objects.filter(kind=DeviceKind.THERMOSTAT).order_by('slug').values_list('slug', flat=True))

    @staticmethod
    def plug_slugs() -> list[str]:
        return list(Device.objects.filter(kind=DeviceKind.PLUG).order_by('slug').values_list('slug', flat=True))

    @staticmethod
    def slugs_of_kind(kind: DeviceKind) -> list[str]:
        return list(Device.objects.filter(kind=kind).order_by('slug').values_list('slug', flat=True))

    @staticmethod
    def slugs_with_driver(driver: DeviceDriver) -> list[str]:
        return list(Device.objects.filter(driver=driver).order_by('slug').values_list('slug', flat=True))

    @staticmethod
    def esphome_devices() -> list[EsphomeDevice]:
        devices = Device.objects.filter(driver__in=[driver.value for driver in ESPHOME_DRIVERS]).order_by('slug')
        return [
            EsphomeDevice(slug=device.slug, node=device.node or device.slug, driver=DeviceDriver(device.driver))
            for device in devices
        ]

    @staticmethod
    def all_settings() -> list[DeviceSettings]:
        return [
            DeviceSettings(
                slug=device.slug,
                name=device.name,
                kind=DeviceKind(device.kind),
                room_slug=None if device.room is None else device.room.slug,
                node=device.node,
            )
            for device in Device.objects.select_related('room').order_by('slug')
        ]
