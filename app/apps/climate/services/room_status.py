from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from apps.climate.domain.air_policy import resolve_control_path
from apps.climate.domain.backup_heat import apply_backup_heat
from apps.climate.domain.drive import PendingKind, PendingWrite, pending_write
from apps.climate.domain.modes import ControlPath
from apps.climate.domain.ports import LoadGate, ZoneStore
from apps.climate.domain.room_status import RoomStatus, ZoneStatus
from apps.climate.domain.zone import ZoneSettings
from apps.climate.domain.zone_intent import ZoneIntent, decide_floor_mode
from apps.climate.repositories.zone import ZoneRepository
from apps.climate.services.room_intents import RoomIntentReader
from apps.climate.services.source_status import SourceStatusReader
from apps.devices.services.state_store import (
    TemperatureStateStore,
    ThermostatStateStore,
    temperature_state_store,
    thermostat_state_store,
)
from apps.power.services.guard_load_gate import GuardLoadGate


class RoomStatusReader:
    def __init__(
        self,
        zones: ZoneStore,
        intents: RoomIntentReader,
        temperatures: TemperatureStateStore,
        thermostats: ThermostatStateStore,
        gate: LoadGate,
        sources: SourceStatusReader,
        dry_run: bool,
    ) -> None:
        self._zones = zones
        self._intents = intents
        self._temperatures = temperatures
        self._thermostats = thermostats
        self._gate = gate
        self._sources = sources
        self._dry_run = dry_run

    @classmethod
    def init(cls) -> 'RoomStatusReader':
        return cls(
            zones=ZoneRepository(),
            intents=RoomIntentReader.init(),
            temperatures=temperature_state_store,
            thermostats=thermostat_state_store,
            gate=GuardLoadGate.init(),
            sources=SourceStatusReader.init(),
            dry_run=settings.TEPLO_DRY_RUN,
        )

    def rooms(self, now: datetime, *, clock_trusted: bool) -> list[RoomStatus]:
        local_now = timezone.localtime(now)
        intents = self._intents.read(local_now, clock_trusted=clock_trusted)
        backed_up = self._intents.backed_up(intents)
        held = self._gate.held_zones()
        zones = sorted(self._zones.all_settings(), key=lambda zone: zone.name)
        sources = self._sources.statuses(intents)
        return [
            RoomStatus(
                slug=room.slug,
                name=room.name,
                air=self._air(room.air_sensor_slug),
                intent=intents.by_room[room.slug],
                adjustable=room.adjustable,
                guest_room=room.guest_room,
                zones=tuple(
                    self._zone(
                        zone,
                        intents.zone_intent(zone, backed_up=backed_up),
                        local_now,
                        clock_trusted=clock_trusted,
                        held=zone.slug in held,
                    )
                    for zone in zones
                    if zone.room_slug == room.slug
                ),
                sources=sources.get(room.slug, ()),
                occupancy=intents.occupancy_of(room.slug),
                follows_occupancy=room.follows_occupancy and not room.has_floor,
            )
            for room in intents.rooms
        ]

    def _zone(
        self,
        zone: ZoneSettings,
        intent: ZoneIntent,
        now: datetime,
        *,
        clock_trusted: bool,
        held: bool,
    ) -> ZoneStatus:
        snapshot = self._thermostats.snapshot(zone.thermostat_slug)
        state = snapshot.state if snapshot.available else None
        air = self._air(zone.air_sensor_slug)
        status = ZoneStatus(
            slug=zone.slug,
            name=zone.name,
            available=state is not None,
            air=air,
            floor_temperature=None if state is None else state.floor_temperature,
            setpoint=None if state is None else state.setpoint,
            power=None if state is None else state.power,
            air_path=resolve_control_path(zone, air_available=air is not None) is ControlPath.AIR,
            floor_target=None,
            pending=None,
        )
        hands_off_until = zone.manual_until if intent.hands_off and not held else None
        if status.air_path and hands_off_until is not None:
            return replace(status, pending=PendingWrite(kind=PendingKind.HANDS_OFF, at=hands_off_until))

        if status.air_path:
            return status

        target = decide_floor_mode(zone, intent.forced, now, clock_trusted=clock_trusted).target
        if intent.backup:
            target = apply_backup_heat(target, zone.setpoints)
        return replace(
            status,
            floor_target=target.setpoint if target.power else None,
            pending=pending_write(
                target,
                state,
                last_write_at=zone.last_command_at,
                now=now,
                held=held,
                writes_live=zone.live_control and not self._dry_run,
                hands_off_until=hands_off_until,
            ),
        )

    def _air(self, slug: str | None) -> Decimal | None:
        if slug is None:
            return None

        snapshot = self._temperatures.snapshot(slug)
        if not snapshot.available or snapshot.state is None:
            return None

        return snapshot.state.temperature
