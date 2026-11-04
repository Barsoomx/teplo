from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from django.conf import settings
from django.utils import timezone

from apps.climate.domain.drive import PendingWrite, pending_write
from apps.climate.domain.heat_sources import HeatSourceKind
from apps.climate.domain.learning import ZoneLearningRecord
from apps.climate.domain.modes import ControlPath, ScheduleMode, ZoneKind
from apps.climate.domain.room_climate import VACANT_AFTER_MINUTES_DEFAULT, RoomClimateSettings
from apps.climate.domain.room_intent import RoomIntent, RoomIntents, guests_active, occupancy_holds
from apps.climate.domain.room_status import SourceStatus
from apps.climate.domain.schedule import ScheduleTransition, next_transition
from apps.climate.domain.zone import ZoneSettings
from apps.climate.domain.zone_cycle import CycleConditions, ZoneConditions, ZoneDecision, decide_zone
from apps.climate.domain.zone_intent import ZoneIntent
from apps.climate.repositories.climate_settings import ClimateSettingsRepository
from apps.climate.repositories.zone import ZoneRepository
from apps.climate.repositories.zone_learning import ZoneLearningRepository
from apps.climate.services.control_book import ControlBook, control_book
from apps.climate.services.room_intents import RoomIntentReader
from apps.climate.services.source_status import SourceStatusReader
from apps.core.domain.usecases.base import BaseUseCase
from apps.devices.domain.device import DeviceSettings
from apps.devices.domain.kinds import DeviceKind
from apps.devices.drivers.esphome_protocol import NODE_PREFIX
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.presence_store import PresenceStore, presence_store
from apps.devices.services.state_store import (
    PlugStateStore,
    TemperatureStateStore,
    ThermostatStateStore,
    plug_state_store,
    temperature_state_store,
    thermostat_state_store,
)
from apps.house.domain.rooms import RoomRecord
from apps.house.repositories.house import HouseRepository
from apps.power.domain.control import LoadControl, commands_live
from apps.power.domain.load_state import LoadState, load_state
from apps.power.domain.load_status import plug_draws, thermostat_draws
from apps.power.domain.loads import AmpsOrigin, LoadKind, LoadPhase, LoadRecord, PhaseOrigin
from apps.power.domain.outage import OutageRecord
from apps.power.domain.shedding import Shed
from apps.power.repositories.load import LoadRepository
from apps.power.repositories.load_settings import LoadSettingsRepository
from apps.power.repositories.outage import OutageRepository
from apps.power.services.guard_book import GuardBook, guard_book
from apps.power.services.outage_overloads import OutageOverloads
from apps.presence.domain.occupancy import Occupancy
from apps.system.domain.clock import ClockStatus, ClockTrustSource
from apps.system.services.clock_trust import clock_trust_source


class ZoneRegime(StrEnum):
    COMFORT = 'comfort'
    PREHEAT = 'preheat'
    NIGHT = 'night'
    BOOST = 'boost'
    OFF = 'off'


@dataclass(frozen=True, kw_only=True)
class GetPanelOverviewInputDTO:
    now: datetime


@dataclass(frozen=True, kw_only=True)
class SensorOverview:
    slug: str
    name: str
    kind: DeviceKind
    available: bool
    error_code: str | None
    error: str | None
    temperature: Decimal | None
    humidity: Decimal | None
    age_seconds: int | None
    room: str | None = None


@dataclass(frozen=True, kw_only=True)
class ZoneOverview:
    slug: str
    name: str
    kind: ZoneKind
    mode: ScheduleMode
    available: bool
    error_code: str | None
    error: str | None
    floor_temperature: Decimal | None
    power: bool | None
    setpoint: Decimal | None
    target_setpoint: Decimal | None
    boosted: bool
    live_control: bool
    air_control_live: bool
    path: ControlPath
    air_target: Decimal | None
    regime: ZoneRegime
    lead: timedelta | None
    boost_until: datetime | None
    next_change: ScheduleTransition | None
    air: SensorOverview | None
    room: str | None = None
    pending: PendingWrite | None = None
    manual_until: datetime | None = None


@dataclass(frozen=True, kw_only=True)
class PlugOverview:
    slug: str
    name: str
    available: bool
    error_code: str | None
    error: str | None
    on: bool | None
    power_watts: Decimal | None
    current_amps: Decimal | None
    voltage_volts: Decimal | None
    room: str | None = None


@dataclass(frozen=True, kw_only=True)
class LoadOverview:
    slug: str
    name: str
    kind: LoadKind
    zone: str | None
    device: str
    priority: int
    phase: LoadPhase
    phase_origin: PhaseOrigin
    learned_phase: LoadPhase | None
    learned_amps: Decimal | None
    learning_samples: int
    nominal_amps: Decimal | None
    amps: Decimal
    amps_origin: AmpsOrigin
    state: LoadState
    live: bool


@dataclass(frozen=True, kw_only=True)
class RoomComfortOverview:
    comfort_target: Decimal
    setback_target: Decimal
    air_sensor: str | None
    backup_zone: str | None
    backup_zone_name: str | None
    backup_below: Decimal
    backup_live: bool
    backup_active: bool


@dataclass(frozen=True, kw_only=True)
class SourceOverview:
    slug: str
    name: str
    kind: HeatSourceKind
    live: bool
    lit_until: datetime | None
    burning: bool
    room: str | None = None
    device: str | None = None
    priority: int | None = None
    available: bool = False
    error_code: str | None = None
    error: str | None = None
    on: bool | None = None
    setpoint: Decimal | None = None
    level: str | None = None
    temperature: Decimal | None = None
    target_on: bool | None = None
    target_setpoint: Decimal | None = None
    commands_live: bool = False
    held_until: datetime | None = None
    shed: bool = False
    pending: PendingWrite | None = None


@dataclass(frozen=True, kw_only=True)
class PresenceOverview:
    slug: str
    name: str
    room: str | None
    node: str
    available: bool
    present: bool | None
    age_seconds: int | None
    seen_age_seconds: int | None
    error_code: str | None
    error: str | None


@dataclass(frozen=True, kw_only=True)
class OccupancyOverview:
    state: Occupancy
    follows: bool
    sources: tuple[PresenceOverview, ...]
    vacant_after_minutes: int = VACANT_AFTER_MINUTES_DEFAULT


@dataclass(frozen=True, kw_only=True)
class RoomOverview:
    slug: str
    name: str
    floor: str
    zones: tuple[ZoneOverview, ...]
    sensors: tuple[SensorOverview, ...]
    plugs: tuple[PlugOverview, ...]
    devices: tuple[str, ...]
    comfort: RoomComfortOverview | None = None
    intent: RoomIntent | None = None
    adjustable: bool = False
    guest_room: bool = False
    sources: tuple[SourceOverview, ...] = ()
    occupancy: OccupancyOverview | None = None
    follows_occupancy: bool = False
    back_to_occupancy: bool = False

    @property
    def shown(self) -> bool:
        return bool(self.zones or self.sensors or self.plugs or self.sources or self.occupancy)


@dataclass(frozen=True, kw_only=True)
class FloorOverview:
    slug: str
    name: str
    rooms: tuple[RoomOverview, ...]

    @property
    def shown_rooms(self) -> tuple[RoomOverview, ...]:
        return tuple(room for room in self.rooms if room.shown)


@dataclass(frozen=True, kw_only=True)
class GetPanelOverviewOutputDTO:
    zones: tuple[ZoneOverview, ...]
    plugs: tuple[PlugOverview, ...]
    loads: tuple[LoadOverview, ...]
    sensors: tuple[SensorOverview, ...]
    outdoor: SensorOverview | None
    standalone_sensors: tuple[SensorOverview, ...]
    floors: tuple[FloorOverview, ...]
    unplaced_zones: tuple[ZoneOverview, ...]
    unplaced_plugs: tuple[PlugOverview, ...]
    outages: tuple[OutageRecord, ...]
    clock: ClockStatus
    dry_run: bool
    guests_until: datetime | None = None
    guest_rooms: tuple[str, ...] = ()
    sources: tuple[SourceOverview, ...] = ()
    presence: tuple[PresenceOverview, ...] = ()


class GetPanelOverviewUseCase(BaseUseCase[GetPanelOverviewInputDTO, GetPanelOverviewOutputDTO]):
    def __init__(
        self,
        zones: ZoneRepository,
        intents: RoomIntentReader,
        devices: DeviceRepository,
        house: HouseRepository,
        climate_settings: ClimateSettingsRepository,
        learning: ZoneLearningRepository,
        outages: OutageRepository,
        overloads: OutageOverloads,
        loads: LoadRepository,
        load_settings: LoadSettingsRepository,
        thermostat_store: ThermostatStateStore,
        plug_store: PlugStateStore,
        temperature_store: TemperatureStateStore,
        presence_store: PresenceStore,
        book: ControlBook,
        guard_book: GuardBook,
        sources: SourceStatusReader,
        clock: ClockTrustSource,
        dry_run: bool,
    ) -> None:
        super().__init__()
        self._zones = zones
        self._intents = intents
        self._devices = devices
        self._house = house
        self._climate_settings = climate_settings
        self._learning = learning
        self._outages = outages
        self._overloads = overloads
        self._loads = loads
        self._load_settings = load_settings
        self._thermostat_store = thermostat_store
        self._plug_store = plug_store
        self._temperature_store = temperature_store
        self._presence_store = presence_store
        self._book = book
        self._guard_book = guard_book
        self._sources = sources
        self._clock = clock
        self._dry_run = dry_run

    @classmethod
    def init(cls) -> 'GetPanelOverviewUseCase':
        return cls(
            zones=ZoneRepository(),
            intents=RoomIntentReader.init(),
            devices=DeviceRepository(),
            house=HouseRepository(),
            climate_settings=ClimateSettingsRepository(),
            learning=ZoneLearningRepository(),
            outages=OutageRepository(),
            overloads=OutageOverloads.init(),
            loads=LoadRepository(),
            load_settings=LoadSettingsRepository(),
            thermostat_store=thermostat_state_store,
            plug_store=plug_state_store,
            temperature_store=temperature_state_store,
            presence_store=presence_store,
            book=control_book,
            guard_book=guard_book,
            sources=SourceStatusReader.init(),
            clock=clock_trust_source(),
            dry_run=settings.TEPLO_DRY_RUN,
        )

    def _execute(self, input_dto: GetPanelOverviewInputDTO) -> GetPanelOverviewOutputDTO:
        now = timezone.localtime(input_dto.now)
        clock = self._clock.status()
        control = self._climate_settings.control()
        learning = self._learning.all_records()
        zones = sorted(self._zones.all_settings(), key=lambda zone: zone.name)
        intents = self._intents.read(now, clock_trusted=clock.trusted)
        guests = self._climate_settings.guests()
        devices = sorted(self._devices.all_settings(), key=lambda device: device.name)
        sensors = {
            device.slug: self._sensor(device) for device in devices if device.kind == DeviceKind.TEMPERATURE_SENSOR
        }
        outdoor_slug = control.sensors.outdoor_slug
        outdoor = None if outdoor_slug is None else sensors.get(outdoor_slug)
        cycle = CycleConditions(now=now, clock_trusted=clock.trusted, control=control, outdoor=_temperature(outdoor))
        backed_up = self._intents.backed_up(intents)
        held = self._guard_book.held_zones()
        zone_overviews = tuple(
            self._zone(
                zone,
                _air_sensor(zone, sensors),
                learning.get(zone.slug, ZoneLearningRecord.unlearned()),
                cycle,
                intent=intents.zone_intent(zone, backed_up=backed_up),
                held=zone.slug in held,
            )
            for zone in zones
        )
        plugs = tuple(self._plug(device) for device in devices if device.kind == DeviceKind.PLUG)
        presence = tuple(self._presence(device) for device in devices if device.kind == DeviceKind.PRESENCE_SENSOR)
        zone_air = {zone.air_sensor_slug for zone in zones}
        room_sensors = tuple(sensor for slug, sensor in sensors.items() if slug not in zone_air)
        sources = _room_sources(intents, self._book.burning_rooms(), self._sources.statuses(intents))
        floors = self._floors(
            zone_overviews,
            room_sensors,
            plugs,
            devices,
            intents,
            self._comforts(intents.rooms, zones),
            sources,
            presence,
            guests.until,
        )
        placed = frozenset(room.slug for floor in floors for room in floor.rooms)
        return GetPanelOverviewOutputDTO(
            zones=zone_overviews,
            plugs=plugs,
            loads=self._load_overviews(
                frozenset(zone.slug for zone in zones if zone.live_control),
                {source.slug: source for room_sources in sources.values() for source in room_sources},
            ),
            sensors=tuple(sensors.values()),
            outdoor=outdoor,
            standalone_sensors=tuple(
                sensor for sensor in room_sensors if sensor.slug != outdoor_slug and sensor.room not in placed
            ),
            floors=floors,
            unplaced_zones=tuple(zone for zone in zone_overviews if zone.room not in placed),
            unplaced_plugs=tuple(plug for plug in plugs if plug.room not in placed),
            outages=tuple(self._overloads.noted(outage) for outage in self._outages.unmarked()),
            clock=clock,
            dry_run=self._dry_run,
            guests_until=guests.until if guests_active(guests.until, now) else None,
            guest_rooms=tuple(room.name for room in intents.rooms if room.guest_room),
            sources=tuple(source for floor in floors for room in floor.rooms for source in sources.get(room.slug, ())),
            presence=presence,
        )

    def _floors(
        self,
        zones: tuple[ZoneOverview, ...],
        sensors: tuple[SensorOverview, ...],
        plugs: tuple[PlugOverview, ...],
        devices: list[DeviceSettings],
        intents: RoomIntents,
        comforts: dict[str, RoomComfortOverview],
        sources: Mapping[str, tuple[SourceOverview, ...]],
        presence: tuple[PresenceOverview, ...],
        guests_until: datetime | None,
    ) -> tuple[FloorOverview, ...]:
        climates = {room.slug: room for room in intents.rooms}
        return tuple(
            FloorOverview(
                slug=floor.slug,
                name=floor.name,
                rooms=tuple(
                    _with_climate(
                        _room(
                            room,
                            zones,
                            sensors,
                            plugs,
                            devices,
                            comforts.get(room.slug),
                            sources.get(room.slug, ()),
                            _occupancy(room.slug, presence, intents, climates.get(room.slug)),
                        ),
                        climates.get(room.slug),
                        intents,
                        guests_until,
                    )
                    for room in floor.rooms
                ),
            )
            for floor in self._house.floors()
        )

    def _comforts(
        self,
        rooms: tuple[RoomClimateSettings, ...],
        zones: list[ZoneSettings],
    ) -> dict[str, RoomComfortOverview]:
        names = {zone.slug: zone.name for zone in zones}
        return {
            room.slug: RoomComfortOverview(
                comfort_target=room.targets.comfort,
                setback_target=room.targets.night,
                air_sensor=room.air_sensor_slug,
                backup_zone=room.backup.zone_slug,
                backup_zone_name=None if room.backup.zone_slug is None else names.get(room.backup.zone_slug),
                backup_below=room.backup.below,
                backup_live=room.backup.live,
                backup_active=self._book.backup_active(room.slug),
            )
            for room in rooms
        }

    def _zone(
        self,
        zone: ZoneSettings,
        air: SensorOverview | None,
        learning: ZoneLearningRecord,
        cycle: CycleConditions,
        *,
        intent: ZoneIntent,
        held: bool,
    ) -> ZoneOverview:
        snapshot = self._thermostat_store.snapshot(zone.thermostat_slug)
        writes_live = zone.live_control and not self._dry_run
        conditions = ZoneConditions(air=_temperature(air), learning=learning, intent=intent, writes_live=writes_live)
        decision = decide_zone(zone, self._book.zone(zone.slug), conditions, cycle)
        forced = bool(intent.forced)
        boosted = zone.is_boosted(cycle.now)
        regime = _regime(zone, decision.mode, boosted=boosted, clock_trusted=cycle.clock_trusted, forced=forced)
        lead = _schedule_lead(zone, decision)
        state = snapshot.state
        manual_until = zone.manual_until if intent.hands_off else None
        return ZoneOverview(
            slug=zone.slug,
            name=zone.name,
            kind=zone.kind,
            mode=decision.mode,
            available=snapshot.available,
            error_code=snapshot.error_code,
            error=snapshot.error,
            floor_temperature=state.floor_temperature if state else None,
            power=state.power if state else None,
            setpoint=state.setpoint if state else None,
            target_setpoint=decision.target.setpoint,
            boosted=boosted,
            live_control=zone.live_control,
            air_control_live=zone.air_control_live,
            path=decision.path,
            air_target=None if decision.plan is None else decision.plan.target,
            regime=regime,
            lead=lead if regime is ZoneRegime.PREHEAT else None,
            boost_until=zone.boost_until if boosted else None,
            next_change=(
                next_transition(zone.windows, cycle.now, lead)
                if cycle.clock_trusted and zone.heating_enabled and not forced
                else None
            ),
            air=air,
            room=zone.room_slug,
            pending=pending_write(
                decision.target,
                state if snapshot.available else None,
                last_write_at=zone.last_command_at,
                now=cycle.now,
                held=held,
                writes_live=writes_live,
                hands_off_until=manual_until,
            ),
            manual_until=manual_until,
        )

    def _load_overviews(
        self,
        live_zones: frozenset[str],
        sources: Mapping[str, SourceOverview],
    ) -> tuple[LoadOverview, ...]:
        control = self._load_settings.control()
        state = self._guard_book.state()
        sheds: Mapping[str, Shed] = {} if state is None else state.shedding.sheds
        waiting = self._guard_book.waiting()
        return tuple(
            self._load(record, control, sheds.get(record.slug), waiting, live_zones, sources)
            for record in self._loads.all_records()
        )

    def _load(
        self,
        record: LoadRecord,
        control: LoadControl,
        shed: Shed | None,
        waiting: frozenset[str],
        live_zones: frozenset[str],
        sources: Mapping[str, SourceOverview],
    ) -> LoadOverview:
        source = None if record.source_slug is None else sources.get(record.source_slug)
        live = commands_live(
            control,
            record,
            dry_run=self._dry_run,
            controller_live=record.zone_slug in live_zones if source is None else source.live,
        )
        learned = record.learned
        return LoadOverview(
            slug=record.slug,
            name=record.name,
            kind=record.kind,
            zone=record.zone_slug,
            device=record.device_slug,
            priority=record.priority,
            phase=record.effective_phase,
            phase_origin=record.phase_origin,
            learned_phase=None if learned is None else learned.phase,
            learned_amps=None if learned is None else learned.amps,
            learning_samples=0 if learned is None else learned.samples,
            nominal_amps=record.nominal_amps,
            amps=record.effective_amps(control.default_amps),
            amps_origin=record.amps_origin,
            state=load_state(
                drawing=self._drawing(record) if source is None else _source_drawing(source),
                shed=shed,
                waiting=record.slug in waiting,
                live=live,
            ),
            live=live,
        )

    def _drawing(self, record: LoadRecord) -> bool | None:
        if record.kind is LoadKind.SOURCE:
            return None

        if record.kind is LoadKind.PLUG:
            plug = self._plug_store.snapshot(record.device_slug)
            if not plug.available or plug.state is None:
                return None

            return plug_draws(on=plug.state.on, current=plug.state.current_amps)

        thermostat = self._thermostat_store.snapshot(record.device_slug)
        if not thermostat.available or thermostat.state is None:
            return None

        return thermostat_draws(
            power=thermostat.state.power,
            heating=thermostat.state.heating,
            setpoint=thermostat.state.setpoint,
            floor_temperature=thermostat.state.floor_temperature,
        )

    def _plug(self, device: DeviceSettings) -> PlugOverview:
        snapshot = self._plug_store.snapshot(device.slug)
        state = snapshot.state
        return PlugOverview(
            slug=device.slug,
            name=device.name,
            available=snapshot.available,
            error_code=snapshot.error_code,
            error=snapshot.error,
            on=state.on if state else None,
            power_watts=state.power_watts if state else None,
            current_amps=state.current_amps if state else None,
            voltage_volts=state.voltage_volts if state else None,
            room=device.room_slug,
        )

    def _presence(self, device: DeviceSettings) -> PresenceOverview:
        snapshot = self._presence_store.snapshot(device.slug)
        return PresenceOverview(
            slug=device.slug,
            name=device.name,
            room=device.room_slug,
            node=f'{NODE_PREFIX}{device.node or device.slug}',
            available=snapshot.available,
            present=snapshot.present,
            age_seconds=_seconds(snapshot.age),
            seen_age_seconds=_seconds(snapshot.seen_age),
            error_code=snapshot.error_code,
            error=snapshot.error,
        )

    def _sensor(self, device: DeviceSettings) -> SensorOverview:
        snapshot = self._temperature_store.snapshot(device.slug)
        reading = snapshot.state
        return SensorOverview(
            slug=device.slug,
            name=device.name,
            kind=device.kind,
            available=snapshot.available,
            error_code=snapshot.error_code,
            error=snapshot.error,
            temperature=reading.temperature if reading else None,
            humidity=reading.humidity if reading else None,
            age_seconds=None if snapshot.age is None else int(snapshot.age.total_seconds()),
            room=device.room_slug,
        )


def _room(
    room: RoomRecord,
    zones: tuple[ZoneOverview, ...],
    sensors: tuple[SensorOverview, ...],
    plugs: tuple[PlugOverview, ...],
    devices: list[DeviceSettings],
    comfort: RoomComfortOverview | None,
    sources: tuple[SourceOverview, ...],
    occupancy: OccupancyOverview | None,
) -> RoomOverview:
    return RoomOverview(
        slug=room.slug,
        name=room.name,
        floor=room.floor_slug,
        zones=tuple(zone for zone in zones if zone.room == room.slug),
        sensors=tuple(sensor for sensor in sensors if sensor.room == room.slug),
        plugs=tuple(plug for plug in plugs if plug.room == room.slug),
        devices=tuple(sorted(device.slug for device in devices if device.room_slug == room.slug)),
        comfort=comfort,
        sources=sources,
        occupancy=occupancy,
    )


def _occupancy(
    room: str,
    presence: tuple[PresenceOverview, ...],
    intents: RoomIntents,
    climate: RoomClimateSettings | None,
) -> OccupancyOverview | None:
    sources = tuple(source for source in presence if source.room == room)
    if not sources:
        return None

    return OccupancyOverview(
        state=intents.occupancy.get(room, Occupancy.UNKNOWN),
        follows=_follows_occupancy(climate),
        sources=sources,
        vacant_after_minutes=VACANT_AFTER_MINUTES_DEFAULT if climate is None else climate.vacant_after_minutes,
    )


def _follows_occupancy(climate: RoomClimateSettings | None) -> bool:
    return climate is not None and climate.follows_occupancy and not climate.has_floor


def _room_sources(
    intents: RoomIntents,
    burning: frozenset[str],
    statuses: Mapping[str, tuple[SourceStatus, ...]],
) -> dict[str, tuple[SourceOverview, ...]]:
    rooms: dict[str, list[SourceOverview]] = {}
    for fireplace in intents.fireplaces:
        rooms.setdefault(fireplace.room_slug, []).append(
            SourceOverview(
                slug=fireplace.slug,
                name=fireplace.name,
                kind=HeatSourceKind.FIREPLACE,
                live=fireplace.live,
                lit_until=fireplace.lit_until if fireplace.lit(intents.now) else None,
                burning=fireplace.room_slug in burning,
                room=fireplace.room_slug,
                priority=fireplace.priority,
            ),
        )
    for room, sources in statuses.items():
        rooms.setdefault(room, []).extend(_commanded_source(source) for source in sources)
    return {room: tuple(sources) for room, sources in rooms.items()}


def _commanded_source(source: SourceStatus) -> SourceOverview:
    return SourceOverview(
        slug=source.slug,
        name=source.name,
        kind=source.kind,
        live=source.live,
        lit_until=None,
        burning=False,
        room=source.room,
        device=source.device,
        priority=source.priority,
        available=source.available,
        error_code=source.error_code,
        error=source.error,
        on=source.on,
        setpoint=source.setpoint,
        level=source.level,
        temperature=source.temperature,
        target_on=source.target_on,
        target_setpoint=source.target_setpoint,
        commands_live=source.commands_live,
        held_until=source.held_until,
        shed=source.shed,
        pending=source.pending,
    )


def _source_drawing(source: SourceOverview) -> bool | None:
    if not source.available:
        return None

    return source.on


def _with_climate(
    room: RoomOverview,
    climate: RoomClimateSettings | None,
    intents: RoomIntents,
    guests_until: datetime | None,
) -> RoomOverview:
    if climate is None:
        return room

    occupancy = intents.occupancy.get(room.slug, Occupancy.UNKNOWN)
    return replace(
        room,
        intent=intents.of(room.slug),
        adjustable=climate.adjustable,
        guest_room=climate.guest_room,
        follows_occupancy=_follows_occupancy(climate),
        back_to_occupancy=occupancy_holds(climate, occupancy, guests_until=guests_until, now=intents.now),
    )


def _seconds(age: timedelta | None) -> int | None:
    return None if age is None else int(age.total_seconds())


def _air_sensor(zone: ZoneSettings, sensors: dict[str, SensorOverview]) -> SensorOverview | None:
    if zone.air_sensor_slug is None:
        return None

    return sensors.get(zone.air_sensor_slug)


def _temperature(sensor: SensorOverview | None) -> Decimal | None:
    if sensor is None or not sensor.available:
        return None

    return sensor.temperature


def _schedule_lead(zone: ZoneSettings, decision: ZoneDecision) -> timedelta:
    if decision.path is not ControlPath.AIR or decision.plan is None:
        return zone.preheat_lead

    return decision.plan.schedule_lead


def _regime(
    zone: ZoneSettings,
    mode: ScheduleMode,
    *,
    boosted: bool,
    clock_trusted: bool,
    forced: bool,
) -> ZoneRegime:
    if not zone.heating_enabled:
        return ZoneRegime.OFF

    if boosted:
        return ZoneRegime.BOOST

    if not clock_trusted and not forced:
        return ZoneRegime.COMFORT

    return ZoneRegime(mode.value)
