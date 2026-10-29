from apps.devices.domain.errors import DeviceNotFoundError
from apps.devices.domain.kinds import DeviceKind
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
