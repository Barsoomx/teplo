from collections.abc import Sequence
from decimal import Decimal

from django.conf import settings

from apps.climate.domain.fast_source import (
    OUTDOOR_READING_FOR,
    SourceTarget,
    pending_source_write,
    room_targets,
    source_lockouts,
)
from apps.climate.domain.heat_sources import SourceSettings
from apps.climate.domain.ports import ClimateSensorsStore, HeatSourceStore, SourceLoadGate
from apps.climate.domain.room_intent import RoomIntents
from apps.climate.domain.room_status import SourceStatus
from apps.climate.repositories.climate_settings import ClimateSettingsRepository
from apps.climate.repositories.heat_source import HeatSourceRepository
from apps.climate.services.control_book import ControlBook, control_book
from apps.devices.services.state_store import (
    ClimateUnitSnapshot,
    ClimateUnitStateStore,
    TemperatureStateStore,
    climate_unit_state_store,
    temperature_state_store,
)
from apps.power.services.guard_load_gate import GuardLoadGate


class SourceStatusReader:
    def __init__(
        self,
        sources: HeatSourceStore,
        units: ClimateUnitStateStore,
        temperatures: TemperatureStateStore,
        climate_settings: ClimateSensorsStore,
        gate: SourceLoadGate,
        book: ControlBook,
        dry_run: bool,
    ) -> None:
        self._sources = sources
        self._units = units
        self._temperatures = temperatures
        self._climate_settings = climate_settings
        self._gate = gate
        self._book = book
        self._dry_run = dry_run

    @classmethod
    def init(cls) -> 'SourceStatusReader':
        return cls(
            sources=HeatSourceRepository(),
            units=climate_unit_state_store,
            temperatures=temperature_state_store,
            climate_settings=ClimateSettingsRepository(),
            gate=GuardLoadGate.init(),
            book=control_book,
            dry_run=settings.TEPLO_DRY_RUN,
        )

    def statuses(self, intents: RoomIntents) -> dict[str, tuple[SourceStatus, ...]]:
        sources = self._sources.commanded_settings()
        if not sources:
            return {}

        held = self._gate.held_sources()
        outdoor = self._outdoor(self._climate_settings.sensors().outdoor_slug)
        lockouts = source_lockouts(sources, outdoor, self._book.source_locked_out)
        locked_out = frozenset(slug for slug, locked in lockouts.items() if locked)
        rooms: dict[str, list[SourceSettings]] = {}
        for source in sources:
            rooms.setdefault(source.room_slug, []).append(source)
        return {
            room: self._room(room, room_sources, intents, held, locked_out, outdoor)
            for room, room_sources in rooms.items()
        }

    def _room(
        self,
        room: str,
        sources: Sequence[SourceSettings],
        intents: RoomIntents,
        held: frozenset[str],
        locked_out: frozenset[str],
        outdoor: Decimal | None,
    ) -> tuple[SourceStatus, ...]:
        snapshots = {source.slug: self._units.snapshot(source.device_slug) for source in sources}
        intent = intents.of(room)
        climate = next((climate for climate in intents.rooms if climate.slug == room), None)
        targets: dict[str, SourceTarget | None] = {source.slug: None for source in sources}
        if intent is not None and climate is not None:
            targets = room_targets(
                sources,
                {slug: snapshot.state if snapshot.available else None for slug, snapshot in snapshots.items()},
                shed=held,
                locked_out=locked_out,
                air=self._temperature(climate.air_sensor_slug),
                target=intent.target,
                now=intents.now,
            )
        return tuple(
            self._status(
                source,
                snapshots[source.slug],
                targets[source.slug],
                intents,
                held=held,
                locked_out=locked_out,
                outdoor=outdoor,
            )
            for source in sources
        )

    def _status(
        self,
        source: SourceSettings,
        snapshot: ClimateUnitSnapshot,
        target: SourceTarget | None,
        intents: RoomIntents,
        *,
        held: frozenset[str],
        locked_out: frozenset[str],
        outdoor: Decimal | None,
    ) -> SourceStatus:
        state = snapshot.state
        commands_live = source.writes_live(dry_run=self._dry_run)
        held_until = source.manual_until if source.held_by_hand(intents.now) else None
        return SourceStatus(
            slug=source.slug,
            name=source.name,
            kind=source.kind,
            room=source.room_slug,
            device=source.device_slug,
            priority=source.priority,
            live=source.live,
            available=snapshot.available,
            error_code=snapshot.error_code,
            error=snapshot.error,
            on=None if state is None else state.on,
            setpoint=None if state is None else state.target,
            level=None if state is None else state.level,
            temperature=None if state is None else state.room_temperature,
            target_on=None if target is None else target.power,
            target_setpoint=None if target is None else target.setpoint,
            commands_live=commands_live,
            held_until=held_until,
            shed=source.slug in held,
            pending=pending_source_write(
                target,
                state if snapshot.available else None,
                last_write_at=source.last_command_at,
                interval=source.write_interval,
                now=intents.now,
                shed=source.slug in held,
                hands_off_until=held_until,
                writes_live=commands_live,
            ),
            mode=None if state is None else state.mode,
            locked_out=source.slug in locked_out,
            outdoor=None if source.min_outdoor is None else outdoor,
        )

    def _temperature(self, slug: str | None) -> Decimal | None:
        if slug is None:
            return None

        snapshot = self._temperatures.snapshot(slug)
        if not snapshot.available or snapshot.state is None:
            return None

        return snapshot.state.temperature

    def _outdoor(self, slug: str | None) -> Decimal | None:
        if slug is None:
            return None

        snapshot = self._temperatures.snapshot(slug)
        if snapshot.state is None or snapshot.age is None or snapshot.age > OUTDOOR_READING_FOR:
            return None

        return snapshot.state.temperature
