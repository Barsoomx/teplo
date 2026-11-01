from apps.climate.repositories.zone import ZoneRepository
from apps.devices.domain.kinds import DeviceKind
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.state_store import ThermostatStateStore, thermostat_state_store
from apps.notifications.domain.alerts import MonitoredDevice


class ThermostatSource:
    def __init__(self, devices: DeviceRepository, zones: ZoneRepository, store: ThermostatStateStore) -> None:
        self._devices = devices
        self._zones = zones
        self._store = store

    @classmethod
    def init(cls) -> 'ThermostatSource':
        return cls(devices=DeviceRepository(), zones=ZoneRepository(), store=thermostat_state_store)

    def devices(self) -> list[MonitoredDevice]:
        names = {zone.thermostat_slug: zone.name for zone in self._zones.all_settings()}
        return [self._device(slug, names.get(slug, slug)) for slug in self._devices.thermostat_slugs()]

    def _device(self, slug: str, name: str) -> MonitoredDevice:
        snapshot = self._store.snapshot(slug)
        return MonitoredDevice(
            slug=slug,
            name=name,
            kind=DeviceKind.THERMOSTAT,
            available=snapshot.available,
            last_seen_age=snapshot.age,
        )
