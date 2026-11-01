from datetime import datetime

from apps.devices.domain.device import DeviceSettings
from apps.devices.domain.kinds import DeviceKind
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.state_store import PlugStateStore, plug_state_store
from apps.notifications.domain.alerts import MonitoredDevice


class PlugSource:
    def __init__(self, devices: DeviceRepository, store: PlugStateStore) -> None:
        self._devices = devices
        self._store = store

    @classmethod
    def init(cls) -> 'PlugSource':
        return cls(devices=DeviceRepository(), store=plug_state_store)

    def devices(self, now: datetime) -> list[MonitoredDevice]:
        return [self._device(device, now) for device in self._devices.all_settings() if device.kind == DeviceKind.PLUG]

    def _device(self, device: DeviceSettings, now: datetime) -> MonitoredDevice:
        snapshot = self._store.snapshot(device.slug, now)
        return MonitoredDevice(
            slug=device.slug,
            name=device.name,
            kind=device.kind,
            available=snapshot.available,
            last_seen_at=snapshot.state.read_at if snapshot.state else None,
        )
