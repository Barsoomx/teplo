from datetime import timedelta

from apps.devices.domain.device import DeviceSettings
from apps.devices.domain.kinds import DeviceKind
from apps.devices.domain.readings import Phase
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.phase_meter_store import PhaseMeterSnapshot, PhaseMeterStore, phase_meter_store
from apps.notifications.domain.alerts import MonitoredDevice


class PhaseMeterSource:
    def __init__(self, devices: DeviceRepository, store: PhaseMeterStore) -> None:
        self._devices = devices
        self._store = store

    @classmethod
    def init(cls) -> 'PhaseMeterSource':
        return cls(devices=DeviceRepository(), store=phase_meter_store)

    def devices(self) -> list[MonitoredDevice]:
        return [
            self._device(device) for device in self._devices.all_settings() if device.kind == DeviceKind.PHASE_METER
        ]

    def _device(self, device: DeviceSettings) -> MonitoredDevice:
        snapshot = self._store.snapshot(device.slug)
        return MonitoredDevice(
            slug=device.slug,
            name=device.name,
            kind=device.kind,
            available=not snapshot.stale,
            last_seen_age=_oldest_phase_age(snapshot),
        )


def _oldest_phase_age(snapshot: PhaseMeterSnapshot) -> timedelta | None:
    if len(snapshot.ages) < len(Phase):
        return None

    return max(snapshot.ages.values())
