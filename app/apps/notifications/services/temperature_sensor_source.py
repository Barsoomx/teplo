from apps.devices.domain.device import DeviceSettings
from apps.devices.domain.kinds import DeviceKind
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.state_store import TemperatureStateStore, temperature_state_store
from apps.notifications.domain.alerts import MonitoredDevice


class TemperatureSensorSource:
    def __init__(self, devices: DeviceRepository, store: TemperatureStateStore) -> None:
        self._devices = devices
        self._store = store

    @classmethod
    def init(cls) -> 'TemperatureSensorSource':
        return cls(devices=DeviceRepository(), store=temperature_state_store)

    def devices(self) -> list[MonitoredDevice]:
        return [
            self._device(device)
            for device in self._devices.all_settings()
            if device.kind == DeviceKind.TEMPERATURE_SENSOR
        ]

    def _device(self, device: DeviceSettings) -> MonitoredDevice:
        snapshot = self._store.snapshot(device.slug)
        return MonitoredDevice(
            slug=device.slug,
            name=device.name,
            kind=device.kind,
            available=snapshot.available,
            last_seen_age=snapshot.age,
        )
