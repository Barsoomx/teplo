from datetime import datetime

from django.utils import timezone

from apps.climate.domain.ports import (
    ClimateGatesStore,
    FireplaceStore,
    GuestsStore,
    OccupancyStore,
    RoomClimateStore,
)
from apps.climate.domain.room_intent import RoomIntents, resolve_room_intents
from apps.climate.repositories.climate_settings import ClimateSettingsRepository
from apps.climate.repositories.heat_source import HeatSourceRepository
from apps.climate.repositories.room_climate import RoomClimateRepository
from apps.climate.services.control_book import ControlBook, control_book
from apps.presence.services.occupancy_book import occupancy_book


class RoomIntentReader:
    def __init__(
        self,
        rooms: RoomClimateStore,
        guests: GuestsStore,
        fireplaces: FireplaceStore,
        gates: ClimateGatesStore,
        book: ControlBook,
        occupancy: OccupancyStore,
    ) -> None:
        self._rooms = rooms
        self._guests = guests
        self._fireplaces = fireplaces
        self._gates = gates
        self._book = book
        self._occupancy = occupancy

    @classmethod
    def init(cls) -> 'RoomIntentReader':
        return cls(
            rooms=RoomClimateRepository(),
            guests=ClimateSettingsRepository(),
            fireplaces=HeatSourceRepository(),
            gates=ClimateSettingsRepository(),
            book=control_book,
            occupancy=occupancy_book,
        )

    def read(self, now: datetime, *, clock_trusted: bool) -> RoomIntents:
        return resolve_room_intents(
            self._rooms.all_settings(),
            now=timezone.localtime(now),
            clock_trusted=clock_trusted,
            guests_until=self._guests.guests().until,
            fireplaces=self._fireplaces.fireplaces(),
            manual_hold_live=self._gates.gates().manual_hold_live,
            occupancy=self._occupancy.snapshot(),
        )

    def backed_up(self, intents: RoomIntents) -> frozenset[str]:
        return frozenset(
            room.backup.zone_slug
            for room in intents.rooms
            if room.backup.zone_slug is not None and room.backup.live and self._book.backup_active(room.slug)
        )
