from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from django.conf import settings
from django.utils import timezone

from apps.climate.domain.floor_policy import decide_floor_target
from apps.climate.domain.modes import ScheduleMode, ZoneKind
from apps.climate.domain.schedule import ScheduleTransition, next_transition, resolve_mode
from apps.climate.domain.zone import ZoneSettings
from apps.climate.repositories.climate_settings import ClimateSettingsRepository
from apps.climate.repositories.zone import ZoneRepository
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
from apps.power.domain.outage import OutageRecord
from apps.power.repositories.outage import OutageRepository
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
    regime: ZoneRegime
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
class GetPanelOverviewOutputDTO:
    zones: tuple[ZoneOverview, ...]
    plugs: tuple[PlugOverview, ...]
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
        outages: OutageRepository,
        thermostat_store: ThermostatStateStore,
        plug_store: PlugStateStore,
        temperature_store: TemperatureStateStore,
        clock: ClockTrustSource,
        dry_run: bool,
    ) -> None:
        super().__init__()
        self._zones = zones
        self._devices = devices
        self._climate_settings = climate_settings
        self._outages = outages
        self._thermostat_store = thermostat_store
        self._plug_store = plug_store
        self._temperature_store = temperature_store
        self._clock = clock
        self._dry_run = dry_run

    @classmethod
    def init(cls) -> 'GetPanelOverviewUseCase':
        return cls(
            zones=ZoneRepository(),
            devices=DeviceRepository(),
            climate_settings=ClimateSettingsRepository(),
            outages=OutageRepository(),
            thermostat_store=thermostat_state_store,
            plug_store=plug_state_store,
            temperature_store=temperature_state_store,
            clock=clock_trust_source(),
            dry_run=settings.TEPLO_DRY_RUN,
        )

    def _execute(self, input_dto: GetPanelOverviewInputDTO) -> GetPanelOverviewOutputDTO:
        now = timezone.localtime(input_dto.now)
        clock = self._clock.status()
        zones = sorted(self._zones.all_settings(), key=lambda zone: zone.name)
        devices = sorted(self._devices.all_settings(), key=lambda device: device.name)
        sensors = {
            device.slug: self._sensor(device) for device in devices if device.kind == DeviceKind.TEMPERATURE_SENSOR
        }
        outdoor_slug = self._climate_settings.sensors().outdoor_slug
        placed = {outdoor_slug, *(zone.air_sensor_slug for zone in zones)}
        return GetPanelOverviewOutputDTO(
            zones=tuple(self._zone(zone, _air_sensor(zone, sensors), now, clock.trusted) for zone in zones),
            plugs=tuple(self._plug(device) for device in devices if device.kind == DeviceKind.PLUG),
            sensors=tuple(sensors.values()),
            outdoor=None if outdoor_slug is None else sensors.get(outdoor_slug),
            standalone_sensors=tuple(sensor for slug, sensor in sensors.items() if slug not in placed),
            outages=tuple(self._outages.unmarked()),
            clock=clock,
            dry_run=self._dry_run,
        )

    def _zone(
        self,
        zone: ZoneSettings,
        air: SensorOverview | None,
        now: datetime,
        clock_trusted: bool,
    ) -> ZoneOverview:
        snapshot = self._thermostat_store.snapshot(zone.thermostat_slug)
        mode = resolve_mode(zone.windows, now, zone.preheat_lead)
        target = decide_floor_target(zone, mode, now, clock_trusted=clock_trusted)
        boosted = zone.is_boosted(now)
        state = snapshot.state
        return ZoneOverview(
            slug=zone.slug,
            name=zone.name,
            kind=zone.kind,
            mode=mode,
            available=snapshot.available,
            error_code=snapshot.error_code,
            error=snapshot.error,
            floor_temperature=state.floor_temperature if state else None,
            power=state.power if state else None,
            setpoint=state.setpoint if state else None,
            target_setpoint=target.setpoint,
            boosted=boosted,
            live_control=zone.live_control,
            regime=_regime(zone, mode, boosted=boosted, clock_trusted=clock_trusted),
            boost_until=zone.boost_until if boosted else None,
            next_change=(
                next_transition(zone.windows, now, zone.preheat_lead)
                if clock_trusted and zone.heating_enabled
                else None
            ),
            air=air,
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


def _regime(zone: ZoneSettings, mode: ScheduleMode, *, boosted: bool, clock_trusted: bool) -> ZoneRegime:
    if not zone.heating_enabled:
        return ZoneRegime.OFF

    if boosted:
        return ZoneRegime.BOOST

    if not clock_trusted:
        return ZoneRegime.COMFORT

    return ZoneRegime(mode.value)
