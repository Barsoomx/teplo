from datetime import datetime

from django.utils import timezone

from apps.climate.domain.floor_policy import decide_floor_target
from apps.climate.domain.schedule import resolve_mode
from apps.climate.domain.zone import ZoneSettings
from apps.climate.repositories.zone import ZoneRepository
from apps.devices.services.state_store import ThermostatStateStore, thermostat_state_store
from apps.notifications.domain.alerts import ZoneHeating


class ZoneHeatingSource:
    def __init__(self, zones: ZoneRepository, store: ThermostatStateStore) -> None:
        self._zones = zones
        self._store = store

    @classmethod
    def init(cls) -> 'ZoneHeatingSource':
        return cls(zones=ZoneRepository(), store=thermostat_state_store)

    def zones(self, now: datetime, *, clock_trusted: bool) -> list[ZoneHeating]:
        local_now = timezone.localtime(now)
        return [self._zone(zone, local_now, clock_trusted) for zone in self._zones.all_settings()]

    def _zone(self, zone: ZoneSettings, now: datetime, clock_trusted: bool) -> ZoneHeating:
        mode = resolve_mode(zone.windows, now, zone.preheat_lead)
        target = decide_floor_target(zone, mode, now, clock_trusted=clock_trusted)
        snapshot = self._store.snapshot(zone.thermostat_slug)
        return ZoneHeating(
            slug=zone.slug,
            name=zone.name,
            should_heat=target.power,
            power=snapshot.state.power if snapshot.available and snapshot.state else None,
        )
