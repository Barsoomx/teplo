from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from django.conf import settings
from django.utils import timezone

from apps.climate.domain.control import ClimateControl
from apps.climate.domain.learning import ZoneLearningRecord
from apps.climate.domain.modes import ControlPath, ScheduleMode, ZoneKind
from apps.climate.domain.schedule import ScheduleTransition, next_transition
from apps.climate.domain.zone import ZoneSettings
from apps.climate.domain.zone_cycle import CycleConditions, ZoneConditions, ZoneDecision, decide_zone
from apps.climate.repositories.climate_settings import ClimateSettingsRepository
from apps.climate.repositories.zone import ZoneRepository
from apps.climate.repositories.zone_learning import ZoneLearningRepository
from apps.climate.services.control_book import ControlBook, control_book
from apps.core.domain.usecases.base import BaseUseCase
from apps.devices.domain.device import DeviceSettings
from apps.devices.domain.kinds import DeviceKind
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.state_store import (
    PlugStateStore,
    TemperatureStateStore,
    ThermostatStateStore,
    plug_state_store,
    temperature_state_store,
    thermostat_state_store,
)
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
class GetPanelOverviewOutputDTO:
    zones: tuple[ZoneOverview, ...]
    plugs: tuple[PlugOverview, ...]
    loads: tuple[LoadOverview, ...]
    sensors: tuple[SensorOverview, ...]
    outdoor: SensorOverview | None
    standalone_sensors: tuple[SensorOverview, ...]
    outages: tuple[OutageRecord, ...]
    clock: ClockStatus
    dry_run: bool


class GetPanelOverviewUseCase(BaseUseCase[GetPanelOverviewInputDTO, GetPanelOverviewOutputDTO]):
    def __init__(
        self,
        zones: ZoneRepository,
        devices: DeviceRepository,
        climate_settings: ClimateSettingsRepository,
        learning: ZoneLearningRepository,
        outages: OutageRepository,
        overloads: OutageOverloads,
        loads: LoadRepository,
        load_settings: LoadSettingsRepository,
        thermostat_store: ThermostatStateStore,
        plug_store: PlugStateStore,
        temperature_store: TemperatureStateStore,
        book: ControlBook,
        guard_book: GuardBook,
        clock: ClockTrustSource,
        dry_run: bool,
    ) -> None:
        super().__init__()
        self._zones = zones
        self._devices = devices
        self._climate_settings = climate_settings
        self._learning = learning
        self._outages = outages
        self._overloads = overloads
        self._loads = loads
        self._load_settings = load_settings
        self._thermostat_store = thermostat_store
        self._plug_store = plug_store
        self._temperature_store = temperature_store
        self._book = book
        self._guard_book = guard_book
        self._clock = clock
        self._dry_run = dry_run

    @classmethod
    def init(cls) -> 'GetPanelOverviewUseCase':
        return cls(
            zones=ZoneRepository(),
            devices=DeviceRepository(),
            climate_settings=ClimateSettingsRepository(),
            learning=ZoneLearningRepository(),
            outages=OutageRepository(),
            overloads=OutageOverloads.init(),
            loads=LoadRepository(),
            load_settings=LoadSettingsRepository(),
            thermostat_store=thermostat_state_store,
            plug_store=plug_state_store,
            temperature_store=temperature_state_store,
            book=control_book,
            guard_book=guard_book,
            clock=clock_trust_source(),
            dry_run=settings.TEPLO_DRY_RUN,
        )

    def _execute(self, input_dto: GetPanelOverviewInputDTO) -> GetPanelOverviewOutputDTO:
        now = timezone.localtime(input_dto.now)
        clock = self._clock.status()
        control = self._climate_settings.control()
        learning = self._learning.all_records()
        zones = sorted(self._zones.all_settings(), key=lambda zone: zone.name)
        devices = sorted(self._devices.all_settings(), key=lambda device: device.name)
        sensors = {
            device.slug: self._sensor(device) for device in devices if device.kind == DeviceKind.TEMPERATURE_SENSOR
        }
        outdoor_slug = control.sensors.outdoor_slug
        outdoor = None if outdoor_slug is None else sensors.get(outdoor_slug)
        cycle = CycleConditions(now=now, clock_trusted=clock.trusted, control=control, outdoor=_temperature(outdoor))
        guarded_zone = self._guarded_zone(control)
        placed = {outdoor_slug, *(zone.air_sensor_slug for zone in zones)}
        return GetPanelOverviewOutputDTO(
            zones=tuple(
                self._zone(
                    zone,
                    _air_sensor(zone, sensors),
                    learning.get(zone.slug, ZoneLearningRecord.unlearned()),
                    cycle,
                    guarded=zone.slug == guarded_zone,
                )
                for zone in zones
            ),
            plugs=tuple(self._plug(device) for device in devices if device.kind == DeviceKind.PLUG),
            loads=self._load_overviews(frozenset(zone.slug for zone in zones if zone.live_control)),
            sensors=tuple(sensors.values()),
            outdoor=outdoor,
            standalone_sensors=tuple(sensor for slug, sensor in sensors.items() if slug not in placed),
            outages=tuple(self._overloads.noted(outage) for outage in self._outages.unmarked()),
            clock=clock,
            dry_run=self._dry_run,
        )

    def _guarded_zone(self, control: ClimateControl) -> str | None:
        guard = control.second_floor_guard
        if not guard.live or not self._book.guard_active():
            return None

        return guard.zone_slug

    def _zone(
        self,
        zone: ZoneSettings,
        air: SensorOverview | None,
        learning: ZoneLearningRecord,
        cycle: CycleConditions,
        *,
        guarded: bool,
    ) -> ZoneOverview:
        snapshot = self._thermostat_store.snapshot(zone.thermostat_slug)
        conditions = ZoneConditions(
            air=_temperature(air),
            learning=learning,
            guarded=guarded,
            writes_live=zone.live_control and not self._dry_run,
        )
        decision = decide_zone(zone, self._book.zone(zone.slug), conditions, cycle)
        boosted = zone.is_boosted(cycle.now)
        regime = _regime(zone, decision.mode, boosted=boosted, clock_trusted=cycle.clock_trusted)
        lead = _schedule_lead(zone, decision)
        state = snapshot.state
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
                next_transition(zone.windows, cycle.now, lead) if cycle.clock_trusted and zone.heating_enabled else None
            ),
            air=air,
        )

    def _load_overviews(self, live_zones: frozenset[str]) -> tuple[LoadOverview, ...]:
        control = self._load_settings.control()
        state = self._guard_book.state()
        sheds: Mapping[str, Shed] = {} if state is None else state.shedding.sheds
        waiting = self._guard_book.waiting()
        return tuple(
            self._load(record, control, sheds.get(record.slug), waiting, live_zones)
            for record in self._loads.all_records()
        )

    def _load(
        self,
        record: LoadRecord,
        control: LoadControl,
        shed: Shed | None,
        waiting: frozenset[str],
        live_zones: frozenset[str],
    ) -> LoadOverview:
        live = commands_live(control, record, dry_run=self._dry_run, zone_live_control=record.zone_slug in live_zones)
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
            state=load_state(drawing=self._drawing(record), shed=shed, waiting=record.slug in waiting, live=live),
            live=live,
        )

    def _drawing(self, record: LoadRecord) -> bool | None:
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
        )


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


def _regime(zone: ZoneSettings, mode: ScheduleMode, *, boosted: bool, clock_trusted: bool) -> ZoneRegime:
    if not zone.heating_enabled:
        return ZoneRegime.OFF

    if boosted:
        return ZoneRegime.BOOST

    if not clock_trusted:
        return ZoneRegime.COMFORT

    return ZoneRegime(mode.value)
