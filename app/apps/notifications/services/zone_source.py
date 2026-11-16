from datetime import datetime

from django.conf import settings
from django.utils import timezone

from apps.climate.domain.room_intent import RoomIntents
from apps.climate.domain.zone import ZoneSettings
from apps.climate.domain.zone_intent import decide_floor_mode
from apps.climate.repositories.zone import ZoneRepository
from apps.climate.services.room_intents import RoomIntentReader
from apps.devices.services.state_store import ThermostatStateStore, thermostat_state_store
from apps.notifications.domain.alerts import ZoneHeating
from apps.notifications.domain.ports import HeldZones
from apps.power.services.guard_book import guard_book


class ZoneHeatingSource:
    def __init__(
        self,
        zones: ZoneRepository,
        intents: RoomIntentReader,
        store: ThermostatStateStore,
        held: HeldZones,
        dry_run: bool,
    ) -> None:
        self._zones = zones
        self._intents = intents
        self._store = store
        self._held = held
        self._dry_run = dry_run

    @classmethod
    def init(cls) -> 'ZoneHeatingSource':
        return cls(
            zones=ZoneRepository(),
            intents=RoomIntentReader.init(),
            store=thermostat_state_store,
            held=guard_book,
            dry_run=settings.TEPLO_DRY_RUN,
        )

    def zones(self, now: datetime, *, clock_trusted: bool) -> list[ZoneHeating]:
        local_now = timezone.localtime(now)
        intents = self._intents.read(local_now, clock_trusted=clock_trusted)
        shed = frozenset() if self._dry_run else self._held.held_zones()
        return [self._zone(zone, intents, local_now, clock_trusted, shed) for zone in self._zones.all_settings()]

    def _zone(
        self,
        zone: ZoneSettings,
        intents: RoomIntents,
        now: datetime,
        clock_trusted: bool,
        shed: frozenset[str],
    ) -> ZoneHeating:
        intent = intents.zone_intent(zone, backed_up=frozenset())
        target = decide_floor_mode(zone, intent.forced, now, clock_trusted=clock_trusted).target
        snapshot = self._store.snapshot(zone.thermostat_slug)
        return ZoneHeating(
            slug=zone.slug,
            name=zone.name,
            should_heat=target.power,
            power=snapshot.state.power if snapshot.available and snapshot.state else None,
            hands_off=intent.hands_off,
            shed=zone.slug in shed,
        )
