import threading
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta
from decimal import Decimal
from functools import partial
from zoneinfo import ZoneInfo

import pytest
from django.db import OperationalError
from structlog.testing import capture_logs

from apps.climate.domain.change import ThermostatChange
from apps.climate.domain.drive import StartAnswer, ZoneStart
from apps.climate.domain.events import (
    AirControlDryRun,
    BackupHeatDryRun,
    ExternalHeatEnded,
    ExternalHeatStarted,
    OptimalStartDryRun,
    ZoneCommandFailed,
    ZoneCommandIssued,
    ZoneMorningRejected,
)
from apps.climate.domain.floor_policy import ThermostatTarget
from apps.climate.domain.learning import AirSample, HeatUpModel, Morning, ZoneLearningRecord
from apps.climate.domain.memory import ZoneMemory
from apps.climate.domain.modes import AirState, ControlPath, ScheduleMode, ZoneCycleOutcome, ZoneKind
from apps.climate.domain.observation import StopWatch
from apps.climate.domain.ports import LoadGate, WriteGate, ZoneLearningStore, ZoneStore
from apps.climate.domain.write_guard import write_allowed
from apps.climate.domain.zone import ZoneSettings
from apps.climate.factories import (
    ClimateSettingsFactory,
    ComfortPeriodFactory,
    HeatSourceFactory,
    RoomClimateFactory,
    ZoneFactory,
    ZoneLearningFactory,
)
from apps.climate.models import ClimateSettings, HeatSource, RoomClimate, Zone, ZoneLearning
from apps.climate.repositories.climate_settings import ClimateSettingsRepository
from apps.climate.repositories.heat_source import HeatSourceRepository
from apps.climate.repositories.room_climate import RoomClimateRepository
from apps.climate.repositories.zone import ZoneRepository
from apps.climate.repositories.zone_learning import ZoneLearningRepository
from apps.climate.services.control_book import ControlBook
from apps.climate.services.room_intents import RoomIntentReader
from apps.climate.services.write_gate import KeyedWriteGate
from apps.climate.stubs import ContendedLockStub, LoadGateStub, ZoneLearningStoreStub, ZoneStoreStub
from apps.climate.usecases.boost_zone import BoostZoneInputDTO, BoostZoneUseCase
from apps.climate.usecases.run_heating_cycle import (
    RunHeatingCycleInputDTO,
    RunHeatingCycleOutputDTO,
    RunHeatingCycleUseCase,
)
from apps.core.domain.events import DomainEvent, EventDispatcher
from apps.core.domain.ports import Transaction
from apps.core.stubs import MonotonicClockStub, TransactionStub
from apps.devices.domain.capabilities import FloorThermostat
from apps.devices.domain.errors import DeviceUnavailableError
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.domain.readings import Phase, TemperatureReading, ThermostatMode, ThermostatState
from apps.devices.factories import DeviceFactory
from apps.devices.models import Device
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.phase_meter_store import PhaseMeterStore
from apps.devices.services.state_store import (
    ClimateUnitStateStore,
    PlugStateStore,
    TemperatureStateStore,
    ThermostatStateStore,
)
from apps.devices.stubs import FloorThermostatStub, SwitchablePlugProviderStub, ThermostatProviderStub
from apps.house.factories import RoomFactory
from apps.power.domain.control import LoadControl, PhaseThresholds
from apps.power.domain.events import StartWouldWaitDryRun
from apps.power.domain.guard import GuardLoads, GuardState, SeenReading
from apps.power.domain.load_status import thermostat_reading
from apps.power.domain.loads import MEASURED_PHASES, LiveShed, LoadKind, LoadPhase, LoadRecord, ShedReason
from apps.power.domain.meter_watch import MeterWatch
from apps.power.domain.phase_watch import OverloadWatch, PhaseWatch
from apps.power.domain.start_gate import StartRefusal
from apps.power.factories import LoadSettingsFactory, ZoneLoadFactory
from apps.power.repositories.load import LoadRepository
from apps.power.repositories.load_settings import LoadSettingsRepository
from apps.power.services.guard_book import GuardBook
from apps.power.services.guard_load_gate import GuardLoadGate
from apps.power.services.load_switcher import LoadSwitcher
from apps.power.services.zone_switch import ThermostatZoneSwitch
from apps.power.stubs import LoadStoreStub, SourceSwitchStub, ZoneWriteStampsStub
from apps.power.usecases.recover_write_holds import RecoverWriteHoldsInputDTO, RecoverWriteHoldsUseCase
from apps.power.usecases.shed_phase_loads import (
    ShedPhaseLoadsInputDTO,
    ShedPhaseLoadsOutputDTO,
    ShedPhaseLoadsUseCase,
)
from apps.power.usecases.watch_phases import WatchPhasesInputDTO, WatchPhasesUseCase

pytestmark = pytest.mark.django_db

MOSCOW = ZoneInfo('Europe/Moscow')
MORNING = datetime(2026, 10, 29, 7, 0, tzinfo=MOSCOW)
PREHEAT = datetime(2026, 10, 29, 4, 0, tzinfo=MOSCOW)
NIGHT = datetime(2026, 10, 29, 1, 0, tzinfo=MOSCOW)
COMFORT_AT = datetime(2026, 10, 29, 6, 30, tzinfo=MOSCOW)
FIXED_START = datetime(2026, 10, 29, 3, 30, tzinfo=MOSCOW)
OPTIMAL_START = datetime(2026, 10, 29, 3, 50, tzinfo=MOSCOW)
CAPPED_START = COMFORT_AT - timedelta(hours=4)
BOOST_END = MORNING + timedelta(hours=2)
GUESTS_LEAVE = datetime(2026, 10, 29, 14, 0, tzinfo=MOSCOW)
STEPPED_FORWARD = MORNING + timedelta(hours=2)
FRESH = timedelta(0)
MINUTE = timedelta(minutes=1)
UNAVAILABLE_AFTER = timedelta(minutes=10)
STALE = timedelta(minutes=11)
KITCHEN = 'thermostat-kitchen'
BATHROOM = 'thermostat-bathroom-2f'
LIVING_ROOM = 'thermostat-living-room'
HALF_HOUR = timedelta(minutes=30)
SECOND = timedelta(seconds=1)
WAIT = 5.0
LOCKED = 'database is locked'
HOUSE_AIR = {'air-kitchen': '20.4', 'outdoor': '-3.4', 'air-second-floor': '18.5'}
MORNING_AIR = (
    (FIXED_START - MINUTE, '20.0'),
    (FIXED_START, '20.0'),
    (FIXED_START + timedelta(minutes=40), '20.1'),
    (FIXED_START + timedelta(hours=1, minutes=40), '20.9'),
    (FIXED_START + timedelta(hours=2, minutes=40), '21.7'),
    (COMFORT_AT - MINUTE, '21.9'),
)
AIR_MODE_MORNING_AIR = (
    (OPTIMAL_START - MINUTE, '20.4', '18.0'),
    (OPTIMAL_START, '20.4', '18.0'),
    (OPTIMAL_START + timedelta(minutes=40), '20.5', '30.0'),
    (OPTIMAL_START + timedelta(hours=1, minutes=40), '21.5', '30.0'),
)
AIR_MODE_STOP = OPTIMAL_START + timedelta(hours=1, minutes=52)
FAST_MORNING_AIR = (
    (FIXED_START - MINUTE, '20.0'),
    (FIXED_START, '20.0'),
    (FIXED_START + timedelta(minutes=10), '20.1'),
    (FIXED_START + timedelta(minutes=40), '21.2'),
    (FIXED_START + timedelta(hours=1), '22.0'),
)
FIRE_MINUTES = 15
HELD_UNTIL = MORNING + timedelta(hours=1)
FIRE_RISE_PER_MINUTE = Decimal('0.03')
STOP_AIR = (
    (MORNING, '21.9', '30.0'),
    (MORNING + timedelta(hours=1), '22.3', '18.0'),
    (MORNING + timedelta(hours=2), '22.1', '18.0'),
)
LOAD_THRESHOLDS = PhaseThresholds(
    start_ceiling=Decimal('20.0'),
    shed=Decimal('24.0'),
    shed_hold=timedelta(seconds=3),
    restore=Decimal('18.0'),
    restore_hold=timedelta(minutes=2),
    shed_interval=timedelta(seconds=1),
    restore_interval=timedelta(minutes=1),
    start_interval=timedelta(seconds=30),
    meter_lost_after=timedelta(seconds=60),
)
KITCHEN_LOAD = LoadRecord(
    slug='kitchen',
    name='Кухня',
    kind=LoadKind.ZONE,
    zone_slug='kitchen',
    device_slug=KITCHEN,
    priority=5,
    phase=LoadPhase.L2,
    nominal_amps=Decimal('8.0'),
    learned=None,
    live=True,
    shed=None,
)
LIVING_ROOM_LOAD = replace(
    KITCHEN_LOAD,
    slug='living-room',
    name='Гостиная',
    zone_slug='living-room',
    device_slug=LIVING_ROOM,
    priority=7,
)
HALLWAY_LOAD = replace(
    KITCHEN_LOAD,
    slug='hallway',
    name='Коридор',
    zone_slug='hallway',
    device_slug='thermostat-hallway',
    priority=4,
)
LIVING_ROOM_HANDED_BACK_BEFORE_A_RESTART = replace(
    LIVING_ROOM_LOAD,
    shed=LiveShed(
        at=MORNING - timedelta(minutes=5),
        reason=ShedReason.OVERLOAD,
        phase=LoadPhase.L2,
        was_on=None,
        device=LIVING_ROOM,
        handed_back=True,
    ),
    last_command_at=MORNING - timedelta(minutes=5),
)
FRESH_METER = MeterWatch(seen=True, stale_since=None)
UNSEEN_METER = MeterWatch(seen=False, stale_since=0.0)
LOST_METER = MeterWatch(seen=True, stale_since=-60.0)
GUARD_METER = 'phase-meter'
HALLWAY_HEATING = SeenReading(
    polled_at=MORNING,
    reading=thermostat_reading(
        power=True,
        heating=True,
        setpoint=Decimal('28.0'),
        floor_temperature=Decimal('24.5'),
        read_at=-5.0,
    ),
)

House = Callable[[ThermostatStateStore, TemperatureStateStore], None]


@dataclass(frozen=True, kw_only=True)
class GuardedHouse:
    clock: MonotonicClockStub
    meter: PhaseMeterStore
    store: ThermostatStateStore
    watch: WatchPhasesUseCase
    shed: ShedPhaseLoadsUseCase
    cycle: RunHeatingCycleUseCase


@dataclass(frozen=True, kw_only=True)
class GatedHouse:
    clock: MonotonicClockStub
    cycle: Callable[[], RunHeatingCycleUseCase]
    locked_cycle: Callable[[], RunHeatingCycleUseCase]
    boost: BoostZoneUseCase


@dataclass(frozen=True, kw_only=True)
class ShedRace:
    thermostat: FloorThermostatStub
    cycle: RunHeatingCycleUseCase
    sheds: list[Future[bool]]


@dataclass(frozen=True, kw_only=True)
class BoostRace:
    thermostat: FloorThermostatStub
    racing_cycle: RunHeatingCycleUseCase
    next_cycle: RunHeatingCycleUseCase


class ConcurrentWriteThermostatStub(FloorThermostatStub):
    def __init__(self, meanwhile: Callable[[], object]) -> None:
        super().__init__(state=None)
        self._meanwhile = meanwhile

    def set_setpoint(self, setpoint: Decimal) -> None:
        super().set_setpoint(setpoint)
        self._meanwhile()


class ConcurrentStampZoneStoreStub(ZoneStoreStub):
    def __init__(self, zones: list[ZoneSettings], meanwhile: Callable[[], object]) -> None:
        super().__init__(zones=zones, errors={})
        self._meanwhile = meanwhile

    def mark_commanded(self, slug: str, at: datetime) -> None:
        super().mark_commanded(slug, at)
        self._meanwhile()


class PausingThermostatStub(FloorThermostatStub):
    def __init__(self, meanwhile: Callable[[], object]) -> None:
        super().__init__(state=None)
        self._meanwhile = meanwhile

    def set_power(self, power: bool) -> None:
        if power:
            self._meanwhile()
        super().set_power(power)


class InFlightPowerOffThermostatStub(FloorThermostatStub):
    def __init__(self, started: threading.Event, finish: threading.Event) -> None:
        super().__init__(state=None)
        self._started = started
        self._finish = finish

    def set_power(self, power: bool) -> None:
        if not power:
            self._started.set()
            assert self._finish.wait(timeout=WAIT)
        super().set_power(power)


class HookStub:
    def __init__(self, action: Callable[[], object] | None) -> None:
        self._action = action
        self._fired = False

    def __call__(self) -> None:
        if self._action is None or self._fired:
            return

        self._fired = True
        self._action()


class InterleavedZoneStoreStub:
    def __init__(
        self,
        *,
        before_read: Callable[[], None],
        after_read: Callable[[], None],
        before_stamp: Callable[[], None],
        after_stamp: Callable[[], None],
    ) -> None:
        self._before_read = before_read
        self._after_read = after_read
        self._before_stamp = before_stamp
        self._after_stamp = after_stamp

    def all_settings(self) -> list[ZoneSettings]:
        self._before_read()
        zones = ZoneRepository.all_settings()
        self._after_read()
        return zones

    def mark_commanded(self, slug: str, at: datetime) -> None:
        self._before_stamp()
        ZoneRepository.mark_commanded(slug, at)
        self._after_stamp()

    def mark_dry_run(self, slug: str, at: datetime) -> None:
        ZoneRepository.mark_dry_run(slug, at)

    def mark_air_dry_run(self, slug: str, at: datetime) -> None:
        ZoneRepository.mark_air_dry_run(slug, at)


class ResetBeforeSaveLearningStub:
    def all_records(self) -> dict[str, ZoneLearningRecord]:
        return ZoneLearningRepository.all_records()

    def save_heat_up(self, slug: str, heat_up: HeatUpModel, *, resets: int) -> bool:
        ZoneLearningRepository.reset(slug)
        return ZoneLearningRepository.save_heat_up(slug, heat_up, resets=resets)

    def save_early_stop_offset(self, slug: str, offset: Decimal, *, resets: int) -> bool:
        ZoneLearningRepository.reset(slug)
        return ZoneLearningRepository.save_early_stop_offset(slug, offset, resets=resets)


class DeferredBoostStoreStub:
    def __init__(self) -> None:
        self._pending: list[tuple[str, datetime, bool]] = []

    def boost(self, slug: str, until: datetime, *, clear_stamps: bool) -> None:
        self._pending.append((slug, until, clear_stamps))

    def store(self) -> None:
        for slug, until, clear_stamps in self._pending:
            ZoneRepository.boost(slug, until, clear_stamps=clear_stamps)
        self._pending.clear()


def _never_contended() -> None:
    raise AssertionError('a zone lock was waited for')


def _state(
    setpoint: str,
    mode: ThermostatMode = ThermostatMode.HOLD,
    read_at: datetime = MORNING,
    heating: bool | None = None,
    power: bool = True,
) -> ThermostatState:
    return ThermostatState(
        power=power,
        mode=mode,
        setpoint=Decimal(setpoint),
        floor_temperature=Decimal('21.0'),
        heating=heating,
        read_at=read_at,
    )


def _reading(temperature: str) -> TemperatureReading:
    return TemperatureReading(temperature=Decimal(temperature), humidity=None, read_at=MORNING)


def _at(now: datetime, *, clock_trusted: bool = True, monotonic: float = 0.0) -> RunHeatingCycleInputDTO:
    return RunHeatingCycleInputDTO(now=now, monotonic=monotonic, clock_trusted=clock_trusted)


def _use_case(
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    thermostats: dict[str, FloorThermostat],
    book: ControlBook,
    dispatcher: EventDispatcher,
    *,
    dry_run: bool = False,
    zones: ZoneStore | None = None,
    learning: ZoneLearningStore | None = None,
    gate: LoadGate | None = None,
    writes: WriteGate | None = None,
    transaction: Transaction | None = None,
) -> RunHeatingCycleUseCase:
    return RunHeatingCycleUseCase(
        zones=ZoneRepository() if zones is None else zones,
        intents=RoomIntentReader(
            rooms=RoomClimateRepository(),
            guests=ClimateSettingsRepository(),
            fireplaces=HeatSourceRepository(),
            gates=ClimateSettingsRepository(),
            book=book,
        ),
        climate_settings=ClimateSettingsRepository(),
        learning=ZoneLearningRepository() if learning is None else learning,
        store=store,
        temperatures=temperatures,
        provider=ThermostatProviderStub(thermostats=thermostats),
        book=book,
        gate=LoadGateStub(held=frozenset(), gated=frozenset(), answers={}) if gate is None else gate,
        writes=_write_gate(MonotonicClockStub(now=0.0)) if writes is None else writes,
        transaction=TransactionStub(meanwhile=None) if transaction is None else transaction,
        dry_run=dry_run,
        dispatcher=dispatcher,
    )


def _write_gate(clock: MonotonicClockStub) -> KeyedWriteGate:
    return KeyedWriteGate(clock=clock, key_lock=threading.Lock)


def _gated_house(
    clock: MonotonicClockStub,
    writes: KeyedWriteGate,
    cycle: Callable[[ZoneStore | None], RunHeatingCycleUseCase],
) -> GatedHouse:
    return GatedHouse(
        clock=clock,
        cycle=lambda: cycle(None),
        locked_cycle=lambda: cycle(
            ZoneStoreStub(
                zones=ZoneRepository.all_settings(),
                errors={('last_command_at', 'kitchen'): OperationalError(LOCKED)},
            ),
        ),
        boost=BoostZoneUseCase(zones=ZoneRepository(), writes=writes, dispatcher=EventDispatcher()),
    )


def _cycle(
    zone: Zone,
    state: ThermostatState,
    thermostat: FloorThermostat,
    dispatcher: EventDispatcher,
    *,
    dry_run: bool = False,
    reading_age: timedelta = FRESH,
) -> RunHeatingCycleUseCase:
    monotonic = MonotonicClockStub(now=0.0)
    store = ThermostatStateStore(unavailable_after=UNAVAILABLE_AFTER, clock=monotonic)
    store.record_state(zone.thermostat.slug, state)
    monotonic.advance(reading_age.total_seconds())
    return _use_case(
        store,
        TemperatureStateStore(unavailable_after=UNAVAILABLE_AFTER, clock=monotonic),
        {zone.thermostat.slug: thermostat},
        ControlBook(),
        dispatcher,
        dry_run=dry_run,
    )


def _sensor(slug: str) -> Device:
    return DeviceFactory.create(slug=slug, kind=DeviceKind.TEMPERATURE_SENSOR, driver=DeviceDriver.ESPHOME_TEMPERATURE)


def _air_zone(*, air_control_live: bool = False) -> Zone:
    zone = ZoneFactory.create(
        slug='kitchen',
        thermostat__slug=KITCHEN,
        air_sensor=_sensor('air-kitchen'),
        live_control=True,
        air_control_live=air_control_live,
    )
    ComfortPeriodFactory.create_batch(7, zone=zone, start=time(6, 30), end=time(23, 30))
    return zone


def _backup_zone() -> Zone:
    zone = ZoneFactory.create(
        slug='bathroom-2f',
        kind=ZoneKind.FLOOR,
        thermostat__slug=BATHROOM,
        live_control=True,
        comfort_floor_setpoint=Decimal('30.0'),
    )
    ComfortPeriodFactory.create_batch(7, zone=zone, start=time(6, 30), end=time(23, 30))
    return zone


def _living_room() -> Zone:
    zone = ZoneFactory.create(slug='living-room', kind=ZoneKind.FLOOR, thermostat__slug=LIVING_ROOM, live_control=True)
    ComfortPeriodFactory.create_batch(7, zone=zone, start=time(6, 30), end=time(23, 30))
    return zone


def _issued(zone: str, setpoint: str, at: datetime) -> ZoneCommandIssued:
    return ZoneCommandIssued(
        zone=zone,
        change=ThermostatChange(setpoint=Decimal(setpoint)),
        dry_run=False,
        occurred_at=at,
    )


def _house_settings(*, backup_live: bool = False) -> RoomClimate:
    ClimateSettingsFactory.create(outdoor_sensor=_sensor('outdoor'))
    return RoomClimateFactory.create(
        room=RoomFactory.create(slug='bedroom', name='Спальня'),
        air_sensor=_sensor('air-second-floor'),
        backup_zone=_backup_zone(),
        backup_live=backup_live,
    )


def _open_space(**fields: object) -> RoomClimate:
    climate = RoomClimateFactory.create(
        room=RoomFactory.create(slug='open-space', name='Гостиная, кухня и прихожая'),
        **fields,
    )
    ComfortPeriodFactory.create_batch(7, zone=None, room_climate=climate, start=time(6, 30), end=time(23, 30))
    return climate


def _open_space_zone(climate: RoomClimate, **fields: object) -> Zone:
    return ZoneFactory.create(
        slug='living-room',
        thermostat__slug=LIVING_ROOM,
        live_control=True,
        room=climate.room,
        **fields,
    )


def _overridden_open_space(target: str, until: datetime) -> RoomClimate:
    return _open_space(override_target=Decimal(target), override_until=until, override_source='panel')


def _learned(zone: Zone, *, mornings: int = 3, rate: str = '0.800') -> ZoneLearning:
    return ZoneLearningFactory.create(
        zone=zone,
        dead_time=timedelta(minutes=40),
        heat_up_rates={'-5': rate},
        mornings=mornings,
    )


def _run_morning(
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    thermostats: dict[str, FloorThermostat],
    book: ControlBook,
    readings: tuple[tuple[datetime, str], ...],
) -> None:
    for at, air in readings:
        temperatures.record_state('air-kitchen', _reading(air))
        _use_case(store, temperatures, thermostats, book, EventDispatcher()).execute(_at(at))


def _run_stop(
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    thermostats: dict[str, FloorThermostat],
    book: ControlBook,
    readings: tuple[tuple[datetime, str, str], ...],
) -> None:
    for at, air, setpoint in readings:
        store.record_state(KITCHEN, _state(setpoint))
        temperatures.record_state('air-kitchen', _reading(air))
        _use_case(store, temperatures, thermostats, book, EventDispatcher()).execute(_at(at))


def _fire_air(minute: int) -> str:
    return str(Decimal('22.1') + FIRE_RISE_PER_MINUTE * minute)


def _read(temperatures: TemperatureStateStore, readings: dict[str, str]) -> None:
    for slug, temperature in readings.items():
        temperatures.record_state(slug, _reading(temperature))


def _watch_stop(
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    thermostats: dict[str, FloorThermostat],
    book: ControlBook,
    *,
    dry_run: bool,
) -> None:
    for at, air, setpoint in STOP_AIR:
        store.record_state(KITCHEN, _state(setpoint))
        temperatures.record_state('air-kitchen', _reading(air))
        _use_case(store, temperatures, thermostats, book, EventDispatcher(), dry_run=dry_run).execute(_at(at))


def _floor_zone(slug: str, thermostat: str, **fields: object) -> Zone:
    zone = ZoneFactory.create(slug=slug, thermostat__slug=thermostat, live_control=True, **fields)
    ComfortPeriodFactory.create_batch(7, zone=zone, start=time(6, 30), end=time(23, 30))
    return zone


def _shed_floor_zone(store: ThermostatStateStore, _temperatures: TemperatureStateStore) -> None:
    _floor_zone('kitchen', KITCHEN, last_command_at=MORNING - HALF_HOUR)
    store.record_state(KITCHEN, _state('22.0', power=False))


def _shed_air_zone(store: ThermostatStateStore, temperatures: TemperatureStateStore) -> None:
    Zone.objects.filter(pk=_air_zone(air_control_live=True).pk).update(last_command_at=MORNING - HALF_HOUR)
    store.record_state(KITCHEN, _state('28.0', power=False))
    temperatures.record_state('air-kitchen', _reading('21.5'))


def _shed_boosted_zone(store: ThermostatStateStore, _temperatures: TemperatureStateStore) -> None:
    _floor_zone('kitchen', KITCHEN, last_command_at=MORNING - HALF_HOUR, boost_until=MORNING + timedelta(hours=1))
    store.record_state(KITCHEN, _state('22.0', power=False))


def _shed_backed_up_zone(store: ThermostatStateStore, temperatures: TemperatureStateStore) -> None:
    _house_settings(backup_live=True)
    Zone.objects.filter(slug='bathroom-2f').update(last_command_at=NIGHT - HALF_HOUR)
    store.record_state(BATHROOM, _state('22.0', power=False))
    temperatures.record_state('air-second-floor', _reading('18.9'))


def _idle_floor_zone(store: ThermostatStateStore, _temperatures: TemperatureStateStore) -> None:
    _floor_zone('kitchen', KITCHEN)
    store.record_state(KITCHEN, _state('22.0', heating=False))


def _idle_air_zone(store: ThermostatStateStore, temperatures: TemperatureStateStore) -> None:
    _air_zone(air_control_live=True)
    store.record_state(KITCHEN, _state('18.0', heating=False))
    temperatures.record_state('air-kitchen', _reading('21.5'))


def _idle_boosted_zone(store: ThermostatStateStore, _temperatures: TemperatureStateStore) -> None:
    _floor_zone('kitchen', KITCHEN, boost_until=MORNING + timedelta(hours=1))
    store.record_state(KITCHEN, _state('22.0', heating=False))


def _watch(
    book: GuardBook,
    *,
    management_live: bool,
    load_live: bool = True,
    meter: MeterWatch = FRESH_METER,
    l2: str = '10.0',
    readings: dict[str, SeenReading] | None = None,
    overloaded: frozenset[LoadPhase] = frozenset(),
) -> None:
    loads = GuardLoads(
        records=tuple(replace(record, live=load_live) for record in (HALLWAY_LOAD, KITCHEN_LOAD, LIVING_ROOM_LOAD)),
        control=LoadControl(live=management_live, thresholds=LOAD_THRESHOLDS, default_amps=Decimal('6.0')),
        live_zones=frozenset({'hallway', 'kitchen', 'living-room'}),
        meter='phase-meter',
        read_at=0.0,
    )
    started = GuardState.started(loads, 0.0)
    currents = (
        {LoadPhase.L1: Decimal('8.0'), LoadPhase.L2: Decimal(l2), LoadPhase.L3: Decimal('4.0')}
        if meter.stale_since is None
        else {}
    )
    book.remember(
        replace(
            started,
            phases=replace(
                started.phases,
                meter=meter,
                currents=currents,
                read_at=dict.fromkeys(currents, 0.0),
                watches={phase: _overloaded_watch(phase in overloaded) for phase in MEASURED_PHASES},
            ),
            readings={} if readings is None else readings,
        ),
    )


def _overloaded_watch(overloaded: bool) -> PhaseWatch:
    return PhaseWatch(live=OverloadWatch(overloaded=overloaded), dry_run=OverloadWatch(overloaded=overloaded))


def _guarded_moment(clock: MonotonicClockStub) -> datetime:
    return MORNING + timedelta(seconds=clock())


def _guard_seconds(house: GuardedHouse, count: int, l2: str | None) -> list[ShedPhaseLoadsOutputDTO]:
    outputs: list[ShedPhaseLoadsOutputDTO] = []
    for _ in range(count):
        house.clock.advance(1)
        now = _guarded_moment(house.clock)
        if l2 is not None:
            house.meter.record_current(GUARD_METER, Phase.A, Decimal('8.0'), now)
            house.meter.record_current(GUARD_METER, Phase.B, Decimal(l2), now)
        house.watch.execute(WatchPhasesInputDTO(now=now))
        outputs.append(house.shed.execute(ShedPhaseLoadsInputDTO(now=now)))
    return outputs


def _poll_kitchen_off(house: GuardedHouse) -> None:
    house.store.record_state(KITCHEN, _state('28.0', power=False, heating=False, read_at=_guarded_moment(house.clock)))


def _guarded_cycle(house: GuardedHouse) -> RunHeatingCycleOutputDTO:
    return house.cycle.execute(_at(_guarded_moment(house.clock), monotonic=house.clock()))


@pytest.fixture
def f_zone() -> Zone:
    zone = ZoneFactory.create(slug='kitchen', thermostat__slug=KITCHEN, live_control=True)
    ComfortPeriodFactory.create_batch(7, zone=zone, start=time(6, 30), end=time(23, 30))
    return zone


@pytest.fixture
def f_thermostat() -> FloorThermostatStub:
    return FloorThermostatStub(state=None)


@pytest.fixture
def f_backup_thermostat() -> FloorThermostatStub:
    return FloorThermostatStub(state=None)


@pytest.fixture
def f_events() -> list[DomainEvent]:
    return []


@pytest.fixture
def dispatcher(f_events: list[DomainEvent]) -> EventDispatcher:
    event_dispatcher = EventDispatcher()
    for event_type in (
        ZoneCommandIssued,
        ZoneCommandFailed,
        AirControlDryRun,
        OptimalStartDryRun,
        BackupHeatDryRun,
        StartWouldWaitDryRun,
        ZoneMorningRejected,
        ExternalHeatStarted,
        ExternalHeatEnded,
    ):
        event_dispatcher.register(event_type, f_events.append)
    return event_dispatcher


@pytest.fixture
def clock() -> MonotonicClockStub:
    return MonotonicClockStub(now=0.0)


@pytest.fixture
def store(clock: MonotonicClockStub) -> ThermostatStateStore:
    return ThermostatStateStore(unavailable_after=UNAVAILABLE_AFTER, clock=clock)


@pytest.fixture
def temperatures(clock: MonotonicClockStub) -> TemperatureStateStore:
    return TemperatureStateStore(unavailable_after=UNAVAILABLE_AFTER, clock=clock)


@pytest.fixture
def book() -> ControlBook:
    return ControlBook()


@pytest.fixture
def f_cycle(f_zone: Zone, f_thermostat: FloorThermostatStub, dispatcher: EventDispatcher) -> RunHeatingCycleUseCase:
    return _cycle(f_zone, _state('22.0'), f_thermostat, dispatcher)


@pytest.fixture
def f_relay_cycle(
    request: pytest.FixtureRequest,
    f_zone: Zone,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    return _cycle(f_zone, _state('22.0', heating=request.param), f_thermostat, dispatcher)


@pytest.fixture
def f_dry_run_cycle(
    f_zone: Zone,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    return _cycle(f_zone, _state('22.0'), f_thermostat, dispatcher, dry_run=True)


@pytest.fixture
def f_observed_cycle(
    f_zone: Zone,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    Zone.objects.filter(pk=f_zone.pk).update(live_control=False)
    return _cycle(f_zone, _state('22.0'), f_thermostat, dispatcher)


@pytest.fixture
def f_clock_behind_cycle(
    f_zone: Zone,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    Zone.objects.filter(pk=f_zone.pk).update(last_command_at=NIGHT + timedelta(hours=2))
    return _cycle(f_zone, _state('22.0', read_at=NIGHT), f_thermostat, dispatcher)


@pytest.fixture
def f_boost_ahead_of_clock_cycle(
    f_zone: Zone,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    Zone.objects.filter(pk=f_zone.pk).update(boost_until=NIGHT + timedelta(hours=5))
    return _cycle(f_zone, _state('22.0', read_at=NIGHT), f_thermostat, dispatcher)


@pytest.fixture
def f_program_mode_cycle(
    f_zone: Zone,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    return _cycle(f_zone, _state('5.0', mode=ThermostatMode.SMART), f_thermostat, dispatcher)


@pytest.fixture
def f_recent_command_cycle(
    f_zone: Zone,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    Zone.objects.filter(pk=f_zone.pk).update(last_command_at=MORNING - timedelta(minutes=10))
    return _cycle(f_zone, _state('22.0'), f_thermostat, dispatcher)


@pytest.fixture
def f_promoted_cycle(
    f_zone: Zone,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    Zone.objects.filter(pk=f_zone.pk).update(last_dry_run_at=MORNING - timedelta(minutes=10))
    return _cycle(f_zone, _state('22.0'), f_thermostat, dispatcher)


@pytest.fixture
def f_recent_dry_run_cycle(
    f_zone: Zone,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    Zone.objects.filter(pk=f_zone.pk).update(last_dry_run_at=MORNING - timedelta(minutes=10))
    return _cycle(f_zone, _state('22.0'), f_thermostat, dispatcher, dry_run=True)


@pytest.fixture
def f_stale_cycle(
    f_zone: Zone,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    return _cycle(f_zone, _state('22.0'), f_thermostat, dispatcher, reading_age=timedelta(minutes=11))


@pytest.fixture
def f_failing_cycle(f_zone: Zone, dispatcher: EventDispatcher) -> RunHeatingCycleUseCase:
    thermostat = FloorThermostatStub(state=None, error=DeviceUnavailableError('timeout'))
    return _cycle(f_zone, _state('22.0'), thermostat, dispatcher)


def test_cycle_applies_comfort_setpoint(
    f_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
    f_zone: Zone,
) -> None:
    output = f_cycle.execute(_at(MORNING))

    assert (output.outcomes, f_thermostat.commands, f_events, Zone.objects.get(pk=f_zone.pk).last_command_at) == (
        {'kitchen': ZoneCycleOutcome.APPLIED},
        [('setpoint', Decimal('28.0'))],
        [
            ZoneCommandIssued(
                zone='kitchen',
                change=ThermostatChange(setpoint=Decimal('28.0')),
                dry_run=False,
                occurred_at=MORNING,
            ),
        ],
        MORNING,
    )


@pytest.mark.parametrize('f_relay_cycle', [True, False, None], ids=['heating', 'idle', 'unknown'], indirect=True)
def test_cycle_writes_the_same_whatever_the_relay_reads(
    f_relay_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
    f_zone: Zone,
) -> None:
    output = f_relay_cycle.execute(_at(MORNING))

    assert (output.outcomes, f_thermostat.commands, f_events, Zone.objects.get(pk=f_zone.pk).last_command_at) == (
        {'kitchen': ZoneCycleOutcome.APPLIED},
        [('setpoint', Decimal('28.0'))],
        [
            ZoneCommandIssued(
                zone='kitchen',
                change=ThermostatChange(setpoint=Decimal('28.0')),
                dry_run=False,
                occurred_at=MORNING,
            ),
        ],
        MORNING,
    )


def test_cycle_preheats_before_comfort(f_cycle: RunHeatingCycleUseCase, f_thermostat: FloorThermostatStub) -> None:
    f_cycle.execute(_at(PREHEAT))

    assert f_thermostat.commands == [('setpoint', Decimal('28.0'))]


def test_cycle_leaves_matching_thermostat_alone(
    f_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    output = f_cycle.execute(_at(NIGHT))

    assert (output.outcomes, f_thermostat.commands) == ({'kitchen': ZoneCycleOutcome.UNCHANGED}, [])


def test_cycle_holds_comfort_when_clock_untrusted(
    f_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    f_cycle.execute(_at(NIGHT, clock_trusted=False))

    assert f_thermostat.commands == [('setpoint', Decimal('28.0'))]


def test_cycle_holds_comfort_when_clock_fell_behind_last_command(
    f_clock_behind_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    output = f_clock_behind_cycle.execute(_at(NIGHT, clock_trusted=False))

    assert (output.outcomes, f_thermostat.commands) == (
        {'kitchen': ZoneCycleOutcome.APPLIED},
        [('setpoint', Decimal('28.0'))],
    )


def test_cycle_ignores_boost_ending_beyond_boost_duration_after_clock_moved_back(
    f_boost_ahead_of_clock_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    output = f_boost_ahead_of_clock_cycle.execute(_at(NIGHT, clock_trusted=False))

    assert (output.outcomes, f_thermostat.commands) == (
        {'kitchen': ZoneCycleOutcome.APPLIED},
        [('setpoint', Decimal('28.0'))],
    )


def test_dry_run_journals_without_writing(
    f_dry_run_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
    f_zone: Zone,
) -> None:
    output = f_dry_run_cycle.execute(_at(MORNING))

    zone = Zone.objects.get(pk=f_zone.pk)
    assert (output.outcomes, f_thermostat.commands, f_events, zone.last_dry_run_at, zone.last_command_at) == (
        {'kitchen': ZoneCycleOutcome.DRY_RUN},
        [],
        [
            ZoneCommandIssued(
                zone='kitchen',
                change=ThermostatChange(setpoint=Decimal('28.0')),
                dry_run=True,
                occurred_at=MORNING,
            ),
        ],
        MORNING,
        None,
    )


def test_zone_without_live_control_stays_in_dry_run(
    f_observed_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
) -> None:
    output = f_observed_cycle.execute(_at(MORNING))

    assert (output.outcomes, f_thermostat.commands, f_events) == (
        {'kitchen': ZoneCycleOutcome.DRY_RUN},
        [],
        [
            ZoneCommandIssued(
                zone='kitchen',
                change=ThermostatChange(setpoint=Decimal('28.0')),
                dry_run=True,
                occurred_at=MORNING,
            ),
        ],
    )


def test_cycle_switches_program_mode_to_hold(
    f_program_mode_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    f_program_mode_cycle.execute(_at(NIGHT))

    assert f_thermostat.commands == [('mode', ThermostatMode.HOLD), ('setpoint', Decimal('22.0'))]


def test_cycle_throttles_zone_commanded_recently(
    f_recent_command_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    output = f_recent_command_cycle.execute(_at(MORNING))

    assert (output.outcomes, f_thermostat.commands) == ({'kitchen': ZoneCycleOutcome.THROTTLED}, [])


def test_cycle_writes_at_once_when_zone_goes_live_after_dry_run(
    f_promoted_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    output = f_promoted_cycle.execute(_at(MORNING))

    assert (output.outcomes, f_thermostat.commands) == (
        {'kitchen': ZoneCycleOutcome.APPLIED},
        [('setpoint', Decimal('28.0'))],
    )


def test_dry_run_throttles_its_journal_like_live_writes(
    f_recent_dry_run_cycle: RunHeatingCycleUseCase,
    f_events: list[DomainEvent],
) -> None:
    output = f_recent_dry_run_cycle.execute(_at(MORNING))

    assert (output.outcomes, f_events) == ({'kitchen': ZoneCycleOutcome.THROTTLED}, [])


def test_cycle_skips_unavailable_thermostat(
    f_stale_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    output = f_stale_cycle.execute(_at(MORNING))

    assert (output.outcomes, f_thermostat.commands) == ({'kitchen': ZoneCycleOutcome.DEVICE_UNAVAILABLE}, [])


def test_cycle_reports_failed_write(f_failing_cycle: RunHeatingCycleUseCase, f_events: list[DomainEvent]) -> None:
    output = f_failing_cycle.execute(_at(MORNING))

    assert (output.outcomes, f_events) == (
        {'kitchen': ZoneCycleOutcome.FAILED},
        [
            ZoneCommandFailed(
                zone='kitchen',
                change=ThermostatChange(setpoint=Decimal('28.0')),
                error='timeout',
                error_code='device_unavailable',
                occurred_at=MORNING,
            ),
        ],
    )


@pytest.fixture
def f_flags_off_cycle(
    request: pytest.FixtureRequest,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
    f_backup_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    kitchen_setpoint, bathroom_setpoint = request.param
    _learned(_air_zone())
    _house_settings()
    store.record_state(KITCHEN, _state(kitchen_setpoint))
    store.record_state(BATHROOM, _state(bathroom_setpoint))
    _read(temperatures, HOUSE_AIR)
    return _use_case(store, temperatures, {KITCHEN: f_thermostat, BATHROOM: f_backup_thermostat}, book, dispatcher)


@pytest.fixture
def observed_thermostats(
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    f_thermostat: FloorThermostatStub,
    f_backup_thermostat: FloorThermostatStub,
) -> dict[str, FloorThermostat]:
    _learned(_air_zone())
    _house_settings()
    store.record_state(KITCHEN, _state('28.0'))
    store.record_state(BATHROOM, _state('30.0'))
    _read(temperatures, HOUSE_AIR)
    return {KITCHEN: f_thermostat, BATHROOM: f_backup_thermostat}


@pytest.fixture
def f_observed_air_cycle(
    observed_thermostats: dict[str, FloorThermostat],
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    return _use_case(store, temperatures, observed_thermostats, book, dispatcher)


@pytest.fixture
def f_journaled_air_cycle(
    observed_thermostats: dict[str, FloorThermostat],
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    _use_case(store, temperatures, observed_thermostats, book, EventDispatcher()).execute(_at(MORNING))
    return _use_case(store, temperatures, observed_thermostats, book, dispatcher)


@pytest.fixture
def f_warmed_air_cycle(
    f_journaled_air_cycle: RunHeatingCycleUseCase,
    temperatures: TemperatureStateStore,
) -> RunHeatingCycleUseCase:
    temperatures.record_state('air-kitchen', _reading('22.0'))
    return f_journaled_air_cycle


@pytest.fixture
def f_held_back_air_cycle(
    f_warmed_air_cycle: RunHeatingCycleUseCase,
    observed_thermostats: dict[str, FloorThermostat],
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
) -> RunHeatingCycleUseCase:
    _use_case(store, temperatures, observed_thermostats, book, EventDispatcher()).execute(
        _at(MORNING + timedelta(minutes=10)),
    )
    return f_warmed_air_cycle


@pytest.fixture
def night_journaled_thermostats(
    observed_thermostats: dict[str, FloorThermostat],
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
) -> dict[str, FloorThermostat]:
    _use_case(store, temperatures, observed_thermostats, book, EventDispatcher()).execute(
        _at(FIXED_START - timedelta(minutes=30)),
    )
    return observed_thermostats


@pytest.fixture
def f_optimal_start_dry_run_cycle(
    night_journaled_thermostats: dict[str, FloorThermostat],
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    return _use_case(store, temperatures, night_journaled_thermostats, book, dispatcher)


@pytest.fixture
def f_optimal_start_journaled_cycle(
    night_journaled_thermostats: dict[str, FloorThermostat],
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    _use_case(store, temperatures, night_journaled_thermostats, book, EventDispatcher()).execute(_at(OPTIMAL_START))
    return _use_case(store, temperatures, night_journaled_thermostats, book, dispatcher)


@pytest.fixture
def f_floor_preheat_cycle(
    night_journaled_thermostats: dict[str, FloorThermostat],
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    _use_case(store, temperatures, night_journaled_thermostats, book, EventDispatcher()).execute(_at(FIXED_START))
    temperatures.record_state('air-kitchen', _reading('20.8'))
    return _use_case(store, temperatures, night_journaled_thermostats, book, dispatcher)


@pytest.fixture
def f_live_air_cycle(
    request: pytest.FixtureRequest,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    air, setpoint = request.param
    _air_zone(air_control_live=True)
    store.record_state(KITCHEN, _state(setpoint))
    temperatures.record_state('air-kitchen', _reading(air))
    return _use_case(store, temperatures, {KITCHEN: f_thermostat}, book, dispatcher)


@pytest.fixture
def f_sensor_back_cycle(
    clock: MonotonicClockStub,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    ZoneLearningFactory.create(zone=_air_zone(air_control_live=True), early_stop_offset=Decimal('0.10'))
    thermostats: dict[str, FloorThermostat] = {KITCHEN: f_thermostat}
    store.record_state(KITCHEN, _state('28.0'))
    temperatures.record_state('air-kitchen', _reading('21.5'))
    _use_case(store, temperatures, thermostats, book, EventDispatcher()).execute(_at(MORNING))
    clock.advance(STALE.total_seconds())
    store.record_state(KITCHEN, _state('30.0'))
    _use_case(store, temperatures, thermostats, book, EventDispatcher()).execute(_at(MORNING + timedelta(minutes=30)))
    store.record_state(KITCHEN, _state('28.0'))
    temperatures.record_state('air-kitchen', _reading('21.8'))
    return _use_case(store, temperatures, thermostats, book, dispatcher)


@pytest.fixture
def f_optimal_start_cycle(
    request: pytest.FixtureRequest,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    mornings, rate, outdoor = request.param
    _learned(_air_zone(air_control_live=True), mornings=mornings, rate=rate)
    ClimateSettingsFactory.create(outdoor_sensor=_sensor('outdoor'))
    store.record_state(KITCHEN, _state('18.0'))
    temperatures.record_state('air-kitchen', _reading('19.4'))
    if outdoor is not None:
        temperatures.record_state('outdoor', _reading(outdoor))
    return _use_case(store, temperatures, {KITCHEN: f_thermostat}, book, dispatcher)


@pytest.fixture
def f_latched_preheat_cycle(
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    _learned(_air_zone(air_control_live=True))
    ClimateSettingsFactory.create(outdoor_sensor=_sensor('outdoor'))
    thermostats: dict[str, FloorThermostat] = {KITCHEN: f_thermostat}
    store.record_state(KITCHEN, _state('18.0'))
    _read(temperatures, {'air-kitchen': '20.4', 'outdoor': '-3.4'})
    _use_case(store, temperatures, thermostats, book, EventDispatcher()).execute(_at(OPTIMAL_START))
    store.record_state(KITCHEN, _state('30.0'))
    temperatures.record_state('air-kitchen', _reading('21.6'))
    return _use_case(store, temperatures, thermostats, book, dispatcher)


@pytest.fixture
def f_clock_doubted_preheat_cycle(
    f_latched_preheat_cycle: RunHeatingCycleUseCase,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
) -> RunHeatingCycleUseCase:
    temperatures.record_state('air-kitchen', _reading('21.5'))
    _use_case(store, temperatures, {KITCHEN: f_thermostat}, book, EventDispatcher()).execute(
        _at(COMFORT_AT - timedelta(hours=1, minutes=30), clock_trusted=False),
    )
    return f_latched_preheat_cycle


@pytest.fixture
def boosted_thermostats(
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
) -> dict[str, FloorThermostat]:
    Zone.objects.filter(pk=_air_zone().pk).update(boost_until=BOOST_END)
    thermostats: dict[str, FloorThermostat] = {KITCHEN: f_thermostat}
    store.record_state(KITCHEN, _state('30.0'))
    temperatures.record_state('air-kitchen', _reading('21.5'))
    _use_case(store, temperatures, thermostats, book, EventDispatcher()).execute(_at(MORNING))
    temperatures.record_state('air-kitchen', _reading('21.9'))
    return thermostats


@pytest.fixture
def f_boosted_air_cycle(
    boosted_thermostats: dict[str, FloorThermostat],
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    return _use_case(store, temperatures, boosted_thermostats, book, dispatcher)


@pytest.fixture
def f_boost_ending_air_cycle(
    boosted_thermostats: dict[str, FloorThermostat],
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    _use_case(store, temperatures, boosted_thermostats, book, EventDispatcher()).execute(
        _at(BOOST_END - timedelta(minutes=10)),
    )
    return _use_case(store, temperatures, boosted_thermostats, book, dispatcher)


@pytest.fixture
def f_air_sensor_back_in_dry_run_cycle(
    clock: MonotonicClockStub,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    _air_zone()
    thermostats: dict[str, FloorThermostat] = {KITCHEN: f_thermostat}
    store.record_state(KITCHEN, _state('22.0'))
    temperatures.record_state('air-kitchen', _reading('20.4'))
    _use_case(store, temperatures, thermostats, book, EventDispatcher()).execute(_at(MORNING))
    clock.advance(STALE.total_seconds())
    store.record_state(KITCHEN, _state('28.0'))
    _use_case(store, temperatures, thermostats, book, EventDispatcher()).execute(_at(MORNING + HALF_HOUR))
    temperatures.record_state('air-kitchen', _reading('20.6'))
    return _use_case(store, temperatures, thermostats, book, dispatcher)


@pytest.fixture
def f_heating_air_dry_run_cycle(
    request: pytest.FixtureRequest,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    _air_zone()
    store.record_state(KITCHEN, _state('22.0'))
    temperatures.record_state('air-kitchen', _reading(request.param))
    book.remember_zone(
        'kitchen',
        ZoneMemory(
            path=ControlPath.AIR_DRY_RUN,
            mode=ScheduleMode.COMFORT,
            air_state=AirState.HEAT,
            would_be_target=ThermostatTarget(power=True, setpoint=Decimal('30.0')),
        ),
    )
    return _use_case(store, temperatures, {KITCHEN: f_thermostat}, book, dispatcher)


@pytest.fixture
def f_backup_cycle(
    request: pytest.FixtureRequest,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_backup_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    live, active, air, setpoint = request.param
    _house_settings(backup_live=live)
    store.record_state(BATHROOM, _state(setpoint))
    if air is not None:
        temperatures.record_state('air-second-floor', _reading(air))
    book.remember_backup('bedroom', active)
    return _use_case(store, temperatures, {BATHROOM: f_backup_thermostat}, book, dispatcher)


@pytest.fixture
def f_bathroom_1f_thermostat() -> FloorThermostatStub:
    return FloorThermostatStub(state=None)


@pytest.fixture
def f_two_backup_rooms_cycle(
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_backup_thermostat: FloorThermostatStub,
    f_bathroom_1f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    _house_settings(backup_live=True)
    RoomClimateFactory.create(
        room=RoomFactory.create(slug='office', name='Кабинет'),
        air_sensor=_sensor('air-office'),
        backup_zone=_floor_zone('bathroom-1f', 'thermostat-bathroom-1f', kind=ZoneKind.FLOOR),
        backup_live=True,
    )
    store.record_state(BATHROOM, _state('22.0'))
    store.record_state('thermostat-bathroom-1f', _state('22.0'))
    _read(temperatures, {'air-second-floor': '19.2', 'air-office': '19.2'})
    book.remember_backup('bedroom', True)
    thermostats: dict[str, FloorThermostat] = {
        BATHROOM: f_backup_thermostat,
        'thermostat-bathroom-1f': f_bathroom_1f_thermostat,
    }
    return _use_case(store, temperatures, thermostats, book, dispatcher)


@pytest.fixture
def morning_thermostats(
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
) -> dict[str, FloorThermostat]:
    _air_zone()
    ClimateSettingsFactory.create(outdoor_sensor=_sensor('outdoor'))
    thermostats: dict[str, FloorThermostat] = {KITCHEN: f_thermostat}
    store.record_state(KITCHEN, _state('28.0'))
    temperatures.record_state('outdoor', _reading('-3.4'))
    for at, air in MORNING_AIR:
        temperatures.record_state('air-kitchen', _reading(air))
        _use_case(store, temperatures, thermostats, book, EventDispatcher()).execute(_at(at))
    return thermostats


@pytest.fixture
def f_morning_cycle(
    morning_thermostats: dict[str, FloorThermostat],
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    return _use_case(store, temperatures, morning_thermostats, book, dispatcher)


@pytest.fixture
def f_air_mode_morning_cycle(
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    _learned(_air_zone(air_control_live=True))
    ClimateSettingsFactory.create(outdoor_sensor=_sensor('outdoor'))
    thermostats: dict[str, FloorThermostat] = {KITCHEN: f_thermostat}
    temperatures.record_state('outdoor', _reading('-3.4'))
    for at, air, setpoint in AIR_MODE_MORNING_AIR:
        store.record_state(KITCHEN, _state(setpoint))
        temperatures.record_state('air-kitchen', _reading(air))
        _use_case(store, temperatures, thermostats, book, EventDispatcher()).execute(_at(at))
    temperatures.record_state('air-kitchen', _reading('21.9'))
    return _use_case(store, temperatures, thermostats, book, dispatcher)


@pytest.fixture
def f_failed_preheat_cycle(
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    _air_zone()
    ClimateSettingsFactory.create(outdoor_sensor=_sensor('outdoor'))
    thermostats: dict[str, FloorThermostat] = {
        KITCHEN: FloorThermostatStub(state=None, error=DeviceUnavailableError('timeout')),
    }
    store.record_state(KITCHEN, _state('22.0'))
    temperatures.record_state('outdoor', _reading('-3.4'))
    for at, air in MORNING_AIR:
        temperatures.record_state('air-kitchen', _reading(air))
        _use_case(store, temperatures, thermostats, book, EventDispatcher()).execute(_at(at))
    return _use_case(store, temperatures, thermostats, book, dispatcher)


@pytest.fixture
def f_learned_morning_cycle(
    morning_thermostats: dict[str, FloorThermostat],
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    _use_case(store, temperatures, morning_thermostats, book, EventDispatcher()).execute(_at(COMFORT_AT))
    return _use_case(store, temperatures, morning_thermostats, book, dispatcher)


@pytest.fixture
def stop_thermostats(
    store: ThermostatStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
) -> dict[str, FloorThermostat]:
    _air_zone(air_control_live=True)
    book.remember_zone(
        'kitchen',
        ZoneMemory(
            path=ControlPath.AIR,
            mode=ScheduleMode.COMFORT,
            air_state=AirState.HEAT,
            heat_target=Decimal('22.0'),
        ),
    )
    store.record_state(KITCHEN, _state('30.0'))
    return {KITCHEN: f_thermostat}


@pytest.fixture
def f_watched_stop_cycle(
    stop_thermostats: dict[str, FloorThermostat],
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    _watch_stop(store, temperatures, stop_thermostats, book, dry_run=False)
    return _use_case(store, temperatures, stop_thermostats, book, dispatcher)


@pytest.fixture
def f_learned_stop_cycle(
    f_watched_stop_cycle: RunHeatingCycleUseCase,
    stop_thermostats: dict[str, FloorThermostat],
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
) -> RunHeatingCycleUseCase:
    _use_case(store, temperatures, stop_thermostats, book, EventDispatcher()).execute(
        _at(MORNING + timedelta(hours=2, minutes=1)),
    )
    return f_watched_stop_cycle


@pytest.fixture
def f_dry_run_stop_cycle(
    stop_thermostats: dict[str, FloorThermostat],
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    _watch_stop(store, temperatures, stop_thermostats, book, dry_run=True)
    return _use_case(store, temperatures, stop_thermostats, book, dispatcher, dry_run=True)


@pytest.mark.parametrize(
    ('f_flags_off_cycle', 'now', 'kitchen', 'bathroom'),
    [
        (('28.0', '30.0'), NIGHT, Decimal('22.0'), Decimal('22.0')),
        (('22.0', '22.0'), FIXED_START + timedelta(minutes=15), Decimal('28.0'), Decimal('30.0')),
        (('22.0', '22.0'), MORNING, Decimal('28.0'), Decimal('30.0')),
    ],
    ids=['night', 'preheat_on_fixed_lead_before_optimal_start', 'comfort'],
    indirect=['f_flags_off_cycle'],
)
def test_cycle_with_air_control_and_backup_heat_off_writes_floor_mode_setpoints(
    f_flags_off_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_backup_thermostat: FloorThermostatStub,
    now: datetime,
    kitchen: Decimal,
    bathroom: Decimal,
) -> None:
    output = f_flags_off_cycle.execute(_at(now))

    assert (output.outcomes, f_thermostat.commands, f_backup_thermostat.commands) == (
        {'bathroom-2f': ZoneCycleOutcome.APPLIED, 'kitchen': ZoneCycleOutcome.APPLIED},
        [('setpoint', kitchen)],
        [('setpoint', bathroom)],
    )


def test_cycle_with_air_control_off_journals_would_be_air_state_and_backup_heat(
    f_observed_air_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_backup_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
) -> None:
    output = f_observed_air_cycle.execute(_at(MORNING))

    assert (
        output.outcomes,
        f_thermostat.commands,
        f_backup_thermostat.commands,
        f_events,
        Zone.objects.get(slug='kitchen').last_air_dry_run_at,
    ) == (
        {'bathroom-2f': ZoneCycleOutcome.UNCHANGED, 'kitchen': ZoneCycleOutcome.UNCHANGED},
        [],
        [],
        [
            BackupHeatDryRun(
                room='bedroom',
                room_name='Спальня',
                zone='bathroom-2f',
                active=True,
                air=Decimal('18.5'),
                setpoint=Decimal('30.0'),
                occurred_at=MORNING,
            ),
            AirControlDryRun(
                zone='kitchen',
                air=Decimal('20.4'),
                target=Decimal('22.0'),
                mode=ScheduleMode.COMFORT,
                state=AirState.HEAT,
                previous_state=None,
                power=True,
                setpoint=Decimal('30.0'),
                start_offset=Decimal('0.30'),
                early_stop_offset=Decimal('0.10'),
                lead_minutes=None,
                occurred_at=MORNING,
            ),
        ],
        MORNING,
    )


def test_air_dry_run_journals_nothing_while_would_be_state_holds(
    f_journaled_air_cycle: RunHeatingCycleUseCase,
    f_events: list[DomainEvent],
) -> None:
    f_journaled_air_cycle.execute(_at(MORNING + MINUTE))

    assert f_events == []


def test_air_dry_run_holds_would_be_change_within_thirty_minutes(
    f_warmed_air_cycle: RunHeatingCycleUseCase,
    f_events: list[DomainEvent],
) -> None:
    f_warmed_air_cycle.execute(_at(MORNING + timedelta(minutes=10)))

    assert (f_events, Zone.objects.get(slug='kitchen').last_air_dry_run_at) == ([], MORNING)


def test_air_dry_run_journals_would_be_change_thirty_minutes_after_the_last(
    f_held_back_air_cycle: RunHeatingCycleUseCase,
    f_events: list[DomainEvent],
) -> None:
    now = MORNING + timedelta(minutes=30)

    f_held_back_air_cycle.execute(_at(now))

    assert (f_events, Zone.objects.get(slug='kitchen').last_air_dry_run_at) == (
        [
            AirControlDryRun(
                zone='kitchen',
                air=Decimal('22.0'),
                target=Decimal('22.0'),
                mode=ScheduleMode.COMFORT,
                state=AirState.IDLE,
                previous_state=AirState.HEAT,
                power=True,
                setpoint=Decimal('18.0'),
                start_offset=Decimal('0.30'),
                early_stop_offset=Decimal('0.10'),
                lead_minutes=None,
                occurred_at=now,
            ),
        ],
        now,
    )


def test_air_dry_run_journals_optimal_start_it_would_use(
    f_optimal_start_dry_run_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
) -> None:
    f_optimal_start_dry_run_cycle.execute(_at(OPTIMAL_START))

    assert (f_thermostat.commands, f_events) == (
        [('setpoint', Decimal('22.0'))],
        [
            OptimalStartDryRun(
                zone='kitchen',
                lead_minutes=160,
                start=OPTIMAL_START,
                fixed_start=FIXED_START,
                occurred_at=OPTIMAL_START,
            ),
            AirControlDryRun(
                zone='kitchen',
                air=Decimal('20.4'),
                target=Decimal('22.0'),
                mode=ScheduleMode.PREHEAT,
                state=AirState.HEAT,
                previous_state=AirState.IDLE,
                power=True,
                setpoint=Decimal('30.0'),
                start_offset=Decimal('0.30'),
                early_stop_offset=Decimal('0.10'),
                lead_minutes=160,
                occurred_at=OPTIMAL_START,
            ),
        ],
    )


def test_air_dry_run_journals_optimal_start_once_per_morning(
    f_optimal_start_journaled_cycle: RunHeatingCycleUseCase,
    f_events: list[DomainEvent],
) -> None:
    f_optimal_start_journaled_cycle.execute(_at(OPTIMAL_START + MINUTE))

    assert f_events == []


def test_air_dry_run_journals_later_optimal_start_when_floor_mode_preheat_begins(
    f_optimal_start_dry_run_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
) -> None:
    f_optimal_start_dry_run_cycle.execute(_at(FIXED_START))

    assert (f_thermostat.commands, f_events) == (
        [('setpoint', Decimal('22.0'))],
        [
            OptimalStartDryRun(
                zone='kitchen',
                lead_minutes=160,
                start=OPTIMAL_START,
                fixed_start=FIXED_START,
                occurred_at=FIXED_START,
            ),
        ],
    )


def test_air_dry_run_preheats_on_start_held_before_floor_mode_warmed_room(
    f_floor_preheat_cycle: RunHeatingCycleUseCase,
    f_events: list[DomainEvent],
) -> None:
    f_floor_preheat_cycle.execute(_at(OPTIMAL_START))

    assert f_events == [
        AirControlDryRun(
            zone='kitchen',
            air=Decimal('20.8'),
            target=Decimal('22.0'),
            mode=ScheduleMode.PREHEAT,
            state=AirState.HEAT,
            previous_state=AirState.IDLE,
            power=True,
            setpoint=Decimal('30.0'),
            start_offset=Decimal('0.30'),
            early_stop_offset=Decimal('0.10'),
            lead_minutes=160,
            occurred_at=OPTIMAL_START,
        ),
    ]


def test_air_dry_run_follows_would_be_stop_under_boost_without_entry(
    f_boosted_air_cycle: RunHeatingCycleUseCase,
    f_events: list[DomainEvent],
) -> None:
    f_boosted_air_cycle.execute(_at(BOOST_END - timedelta(minutes=10)))

    assert (f_events, Zone.objects.get(slug='kitchen').last_air_dry_run_at) == ([], MORNING)


def test_air_dry_run_journals_idle_setpoint_when_boost_ends_as_live_air_mode_writes_it(
    f_boost_ending_air_cycle: RunHeatingCycleUseCase,
    f_events: list[DomainEvent],
) -> None:
    f_boost_ending_air_cycle.execute(_at(BOOST_END))

    assert (f_events, Zone.objects.get(slug='kitchen').last_air_dry_run_at) == (
        [
            _issued('kitchen', '28.0', BOOST_END),
            AirControlDryRun(
                zone='kitchen',
                air=Decimal('21.9'),
                target=Decimal('22.0'),
                mode=ScheduleMode.COMFORT,
                state=AirState.IDLE,
                previous_state=AirState.IDLE,
                power=True,
                setpoint=Decimal('18.0'),
                start_offset=Decimal('0.30'),
                early_stop_offset=Decimal('0.10'),
                lead_minutes=None,
                occurred_at=BOOST_END,
            ),
        ],
        BOOST_END,
    )


def test_air_dry_run_back_from_a_lost_sensor_journals_the_change_from_what_floor_mode_wrote(
    f_air_sensor_back_in_dry_run_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
) -> None:
    now = MORNING + HALF_HOUR + MINUTE

    output = f_air_sensor_back_in_dry_run_cycle.execute(_at(now))

    zone = Zone.objects.get(slug='kitchen')
    assert (output.outcomes, f_thermostat.commands, zone.last_command_at, zone.last_air_dry_run_at, f_events) == (
        {'kitchen': ZoneCycleOutcome.UNCHANGED},
        [('setpoint', Decimal('28.0'))],
        MORNING,
        now,
        [
            AirControlDryRun(
                zone='kitchen',
                air=Decimal('20.6'),
                target=Decimal('22.0'),
                mode=ScheduleMode.COMFORT,
                state=AirState.HEAT,
                previous_state=None,
                power=True,
                setpoint=Decimal('30.0'),
                start_offset=Decimal('0.30'),
                early_stop_offset=Decimal('0.10'),
                lead_minutes=None,
                occurred_at=now,
            ),
        ],
    )


@pytest.mark.parametrize(
    ('f_heating_air_dry_run_cycle', 'entries'),
    [
        ('21.8', []),
        (
            '21.9',
            [
                AirControlDryRun(
                    zone='kitchen',
                    air=Decimal('21.9'),
                    target=Decimal('22.0'),
                    mode=ScheduleMode.COMFORT,
                    state=AirState.IDLE,
                    previous_state=AirState.HEAT,
                    power=True,
                    setpoint=Decimal('18.0'),
                    start_offset=Decimal('0.30'),
                    early_stop_offset=Decimal('0.10'),
                    lead_minutes=None,
                    occurred_at=MORNING,
                ),
            ],
        ),
    ],
    ids=['inside_the_band', 'at_the_top_of_the_band'],
    indirect=['f_heating_air_dry_run_cycle'],
)
def test_minimum_band_moves_only_the_air_dry_run_journal_while_floor_mode_writes_as_before(
    f_heating_air_dry_run_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
    entries: list[DomainEvent],
) -> None:
    output = f_heating_air_dry_run_cycle.execute(_at(MORNING))

    assert (output.outcomes, f_thermostat.commands, Zone.objects.get(slug='kitchen').last_command_at, f_events) == (
        {'kitchen': ZoneCycleOutcome.APPLIED},
        [('setpoint', Decimal('28.0'))],
        MORNING,
        [_issued('kitchen', '28.0', MORNING), *entries],
    )


@pytest.mark.parametrize(
    ('f_live_air_cycle', 'now', 'setpoint'),
    [
        (('21.5', '28.0'), MORNING, Decimal('30.0')),
        (('21.8', '28.0'), MORNING, Decimal('18.0')),
        (('19.1', '22.0'), NIGHT, Decimal('30.0')),
        (('19.4', '22.0'), NIGHT, Decimal('18.0')),
    ],
    ids=['comfort_heat_at_floor_limit', 'comfort_idle', 'night_heat_at_floor_limit', 'night_idle'],
    indirect=['f_live_air_cycle'],
)
def test_live_air_zone_drives_floor_between_limit_and_idle_setpoint(
    f_live_air_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
    now: datetime,
    setpoint: Decimal,
) -> None:
    output = f_live_air_cycle.execute(_at(now))

    assert (output.outcomes, f_thermostat.commands, f_events) == (
        {'kitchen': ZoneCycleOutcome.APPLIED},
        [('setpoint', setpoint)],
        [
            ZoneCommandIssued(
                zone='kitchen',
                change=ThermostatChange(setpoint=setpoint),
                dry_run=False,
                occurred_at=now,
            ),
        ],
    )


@pytest.mark.parametrize('f_live_air_cycle', [('21.5', '18.0')], indirect=True)
def test_live_air_zone_holds_comfort_air_target_while_clock_untrusted(
    f_live_air_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    f_live_air_cycle.execute(_at(NIGHT, clock_trusted=False))

    assert f_thermostat.commands == [('setpoint', Decimal('30.0'))]


def test_live_air_zone_falls_back_to_floor_mode_and_reenters_air_mode_idle(
    f_sensor_back_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    output = f_sensor_back_cycle.execute(_at(MORNING + timedelta(hours=1)))

    assert (output.outcomes, f_thermostat.commands) == (
        {'kitchen': ZoneCycleOutcome.APPLIED},
        [('setpoint', Decimal('30.0')), ('setpoint', Decimal('28.0')), ('setpoint', Decimal('18.0'))],
    )


@pytest.mark.parametrize(
    ('f_optimal_start_cycle', 'now', 'commands'),
    [
        ((2, '0.800', '-3.4'), FIXED_START - MINUTE, []),
        ((2, '0.800', '-3.4'), FIXED_START, [('setpoint', Decimal('30.0'))]),
        ((3, '0.800', None), FIXED_START - MINUTE, []),
        ((3, '0.800', None), FIXED_START, [('setpoint', Decimal('30.0'))]),
        ((3, '0.800', '-3.4'), COMFORT_AT - timedelta(hours=3, minutes=56), []),
        ((3, '0.800', '-3.4'), COMFORT_AT - timedelta(hours=3, minutes=55), [('setpoint', Decimal('30.0'))]),
        ((3, '0.500', '-3.4'), COMFORT_AT - timedelta(hours=4, minutes=1), []),
        ((3, '0.500', '-3.4'), COMFORT_AT - timedelta(hours=4), [('setpoint', Decimal('30.0'))]),
    ],
    ids=[
        'fixed_lead_until_three_mornings_before_start',
        'fixed_lead_until_three_mornings_at_start',
        'fixed_lead_without_outdoor_reading_before_start',
        'fixed_lead_without_outdoor_reading_at_start',
        'learned_lead_before_start',
        'learned_lead_at_start',
        'capped_lead_before_start',
        'capped_lead_at_start',
    ],
    indirect=['f_optimal_start_cycle'],
)
def test_live_air_zone_starts_preheat_on_optimal_lead(
    f_optimal_start_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    now: datetime,
    commands: list[tuple[str, object]],
) -> None:
    f_optimal_start_cycle.execute(_at(now))

    assert f_thermostat.commands == commands


def test_live_air_zone_keeps_preheat_started_while_room_warms(
    f_latched_preheat_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    output = f_latched_preheat_cycle.execute(_at(COMFORT_AT - timedelta(hours=1, minutes=20)))

    assert (output.outcomes, f_thermostat.commands) == (
        {'kitchen': ZoneCycleOutcome.UNCHANGED},
        [('setpoint', Decimal('30.0'))],
    )


def test_live_air_zone_keeps_preheat_started_through_a_minute_of_doubted_clock(
    f_clock_doubted_preheat_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    output = f_clock_doubted_preheat_cycle.execute(_at(COMFORT_AT - timedelta(hours=1, minutes=29)))

    assert (output.outcomes, f_thermostat.commands) == (
        {'kitchen': ZoneCycleOutcome.UNCHANGED},
        [('setpoint', Decimal('30.0'))],
    )


@pytest.mark.parametrize(
    ('f_backup_cycle', 'commands'),
    [
        ((True, False, '18.9', '22.0'), [('setpoint', Decimal('30.0'))]),
        ((True, True, '19.2', '30.0'), []),
        ((True, True, '19.3', '30.0'), [('setpoint', Decimal('22.0'))]),
        ((True, True, None, '30.0'), [('setpoint', Decimal('22.0'))]),
    ],
    ids=['turns_on_below_threshold', 'holds_within_hysteresis', 'releases_above_hysteresis', 'releases_on_lost_sensor'],
    indirect=['f_backup_cycle'],
)
def test_live_backup_heat_raises_its_zone_to_comfort_floor_setpoint_with_hysteresis(
    f_backup_cycle: RunHeatingCycleUseCase,
    f_backup_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
    commands: list[tuple[str, Decimal]],
) -> None:
    f_backup_cycle.execute(_at(NIGHT))

    assert (f_backup_thermostat.commands, f_events) == (
        commands,
        [
            ZoneCommandIssued(
                zone='bathroom-2f',
                change=ThermostatChange(setpoint=setpoint),
                dry_run=False,
                occurred_at=NIGHT,
            )
            for _, setpoint in commands
        ],
    )


@pytest.mark.parametrize(
    ('f_backup_cycle', 'events'),
    [
        (
            (False, False, '18.9', '22.0'),
            [
                BackupHeatDryRun(
                    room='bedroom',
                    room_name='Спальня',
                    zone='bathroom-2f',
                    active=True,
                    air=Decimal('18.9'),
                    setpoint=Decimal('30.0'),
                    occurred_at=NIGHT,
                ),
            ],
        ),
        ((False, True, '19.2', '22.0'), []),
        (
            (False, True, '19.3', '22.0'),
            [
                BackupHeatDryRun(
                    room='bedroom',
                    room_name='Спальня',
                    zone='bathroom-2f',
                    active=False,
                    air=Decimal('19.3'),
                    setpoint=Decimal('30.0'),
                    occurred_at=NIGHT,
                ),
            ],
        ),
        (
            (False, True, None, '22.0'),
            [
                BackupHeatDryRun(
                    room='bedroom',
                    room_name='Спальня',
                    zone='bathroom-2f',
                    active=False,
                    air=None,
                    setpoint=Decimal('30.0'),
                    occurred_at=NIGHT,
                ),
            ],
        ),
    ],
    ids=['would_turn_on', 'would_hold_within_hysteresis', 'would_release', 'would_release_on_lost_sensor'],
    indirect=['f_backup_cycle'],
)
def test_backup_heat_with_its_gate_off_journals_what_it_would_do(
    f_backup_cycle: RunHeatingCycleUseCase,
    f_backup_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
    events: list[DomainEvent],
) -> None:
    f_backup_cycle.execute(_at(NIGHT))

    assert (f_backup_thermostat.commands, f_events) == ([], events)


def test_two_rooms_keep_their_backup_heat_apart(
    f_two_backup_rooms_cycle: RunHeatingCycleUseCase,
    f_backup_thermostat: FloorThermostatStub,
    f_bathroom_1f_thermostat: FloorThermostatStub,
    book: ControlBook,
) -> None:
    output = f_two_backup_rooms_cycle.execute(_at(NIGHT))

    assert (
        output.outcomes,
        f_backup_thermostat.commands,
        f_bathroom_1f_thermostat.commands,
        (book.backup_active('bedroom'), book.backup_active('office')),
    ) == (
        {'bathroom-1f': ZoneCycleOutcome.UNCHANGED, 'bathroom-2f': ZoneCycleOutcome.APPLIED},
        [('setpoint', Decimal('30.0'))],
        [],
        (True, False),
    )


@pytest.fixture
def f_guests_cycle(
    request: pytest.FixtureRequest,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_living_room_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    guest_room, guests_until = request.param
    _open_space_zone(_open_space(guest_room=guest_room), kind=ZoneKind.FLOOR)
    ClimateSettingsFactory.create(guests_until=guests_until)
    store.record_state(LIVING_ROOM, _state('22.0'))
    return _use_case(store, temperatures, {LIVING_ROOM: f_living_room_thermostat}, book, dispatcher)


@pytest.fixture
def f_overridden_cycle(
    request: pytest.FixtureRequest,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_living_room_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    now, target, setpoint = request.param
    _open_space_zone(_overridden_open_space(target, now + timedelta(hours=1)), kind=ZoneKind.FLOOR)
    store.record_state(LIVING_ROOM, _state(setpoint))
    return _use_case(store, temperatures, {LIVING_ROOM: f_living_room_thermostat}, book, dispatcher)


@pytest.fixture
def f_guests_stepped_down_cycle(
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_living_room_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    climate = _open_space(
        guest_room=True,
        override_target=Decimal('21.5'),
        override_until=NIGHT + timedelta(hours=1),
        override_source='panel',
    )
    _open_space_zone(climate, kind=ZoneKind.FLOOR)
    ClimateSettingsFactory.create(guests_until=GUESTS_LEAVE)
    store.record_state(LIVING_ROOM, _state('28.0'))
    return _use_case(store, temperatures, {LIVING_ROOM: f_living_room_thermostat}, book, dispatcher)


@pytest.fixture
def f_card_save() -> TransactionStub:
    climate = _overridden_open_space('22.5', MORNING + timedelta(hours=1))
    _open_space_zone(climate, kind=ZoneKind.FLOOR)
    return TransactionStub(
        meanwhile=partial(RoomClimate.objects.filter(pk=climate.pk).update, comfort_target=Decimal('23.0')),
    )


@pytest.fixture
def f_card_saved_cycle(
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_living_room_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
    f_card_save: TransactionStub,
) -> RunHeatingCycleUseCase:
    store.record_state(LIVING_ROOM, _state('28.0'))
    return _use_case(
        store,
        temperatures,
        {LIVING_ROOM: f_living_room_thermostat},
        book,
        dispatcher,
        transaction=f_card_save,
    )


@pytest.fixture
def f_recently_written_overridden_cycle(
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_living_room_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    _open_space_zone(
        _overridden_open_space('22.5', MORNING + timedelta(hours=1)),
        kind=ZoneKind.FLOOR,
        last_command_at=MORNING - timedelta(minutes=10),
    )
    store.record_state(LIVING_ROOM, _state('28.0'))
    return _use_case(store, temperatures, {LIVING_ROOM: f_living_room_thermostat}, book, dispatcher)


@pytest.fixture
def f_expired_override_cycle(
    request: pytest.FixtureRequest,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_living_room_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    _open_space_zone(_overridden_open_space('20.0', request.param), kind=ZoneKind.FLOOR)
    store.record_state(LIVING_ROOM, _state('28.0'))
    return _use_case(store, temperatures, {LIVING_ROOM: f_living_room_thermostat}, book, dispatcher)


@pytest.fixture
def f_overridden_air_cycle(
    request: pytest.FixtureRequest,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_living_room_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    climate = _open_space() if request.param is None else _overridden_open_space(request.param, MORNING + HALF_HOUR)
    _open_space_zone(climate, air_sensor=_sensor('air-living-room'), air_control_live=True)
    store.record_state(LIVING_ROOM, _state('18.0'))
    temperatures.record_state('air-living-room', _reading('21.9'))
    return _use_case(store, temperatures, {LIVING_ROOM: f_living_room_thermostat}, book, dispatcher)


@pytest.mark.parametrize(
    ('f_guests_cycle', 'outcome', 'commands'),
    [
        ((True, GUESTS_LEAVE), ZoneCycleOutcome.APPLIED, [('setpoint', Decimal('28.0'))]),
        ((False, GUESTS_LEAVE), ZoneCycleOutcome.UNCHANGED, []),
        ((True, NIGHT - MINUTE), ZoneCycleOutcome.UNCHANGED, []),
        ((True, NIGHT + timedelta(days=4, minutes=1)), ZoneCycleOutcome.UNCHANGED, []),
    ],
    ids=['guest_room_at_comfort', 'other_room_at_night', 'guests_gone', 'guests_beyond_four_days_ignored'],
    indirect=['f_guests_cycle'],
)
def test_guests_hold_a_guest_room_floor_at_comfort_through_the_night(
    f_guests_cycle: RunHeatingCycleUseCase,
    f_living_room_thermostat: FloorThermostatStub,
    outcome: ZoneCycleOutcome,
    commands: list[tuple[str, Decimal]],
) -> None:
    output = f_guests_cycle.execute(_at(NIGHT))

    assert (output.outcomes, f_living_room_thermostat.commands) == ({'living-room': outcome}, commands)


@pytest.mark.parametrize(
    ('f_overridden_cycle', 'now', 'commands'),
    [
        ((NIGHT, '20.0', '22.0'), NIGHT, [('setpoint', Decimal('28.0'))]),
        ((MORNING, '22.5', '28.0'), MORNING, [('setpoint', Decimal('30.0'))]),
        ((MORNING, '21.5', '28.0'), MORNING, [('setpoint', Decimal('22.0'))]),
        ((MORNING, '22.0', '28.0'), MORNING, []),
        ((PREHEAT, '20.0', '28.0'), PREHEAT, []),
    ],
    ids=[
        'night_up_to_comfort',
        'comfort_up_to_boost',
        'comfort_down_to_night',
        'equal_keeps_comfort',
        'step_up_from_the_setback_keeps_a_preheat',
    ],
    indirect=['f_overridden_cycle'],
)
def test_override_moves_the_floor_one_setpoint_only_the_way_it_moves_the_room(
    f_overridden_cycle: RunHeatingCycleUseCase,
    f_living_room_thermostat: FloorThermostatStub,
    now: datetime,
    commands: list[tuple[str, Decimal]],
) -> None:
    f_overridden_cycle.execute(_at(now))

    assert f_living_room_thermostat.commands == commands


def test_heating_cycle_reads_the_zones_and_the_room_intents_from_one_transaction(
    f_card_saved_cycle: RunHeatingCycleUseCase,
    f_card_save: TransactionStub,
    f_living_room_thermostat: FloorThermostatStub,
) -> None:
    f_card_saved_cycle.execute(_at(MORNING))

    assert (f_card_save.outcomes, f_living_room_thermostat.commands) == (
        ['committed'],
        [('setpoint', Decimal('22.0'))],
    )


def test_a_step_down_from_guests_takes_the_floor_to_its_night_setpoint(
    f_guests_stepped_down_cycle: RunHeatingCycleUseCase,
    f_living_room_thermostat: FloorThermostatStub,
) -> None:
    output = f_guests_stepped_down_cycle.execute(_at(NIGHT))

    assert (output.outcomes, f_living_room_thermostat.commands) == (
        {'living-room': ZoneCycleOutcome.APPLIED},
        [('setpoint', Decimal('22.0'))],
    )


def test_override_reaches_the_floor_at_its_next_allowed_write_only(
    f_recently_written_overridden_cycle: RunHeatingCycleUseCase,
    f_living_room_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
) -> None:
    held = f_recently_written_overridden_cycle.execute(_at(MORNING))
    written = f_recently_written_overridden_cycle.execute(_at(MORNING + timedelta(minutes=20)))

    assert (held.outcomes, written.outcomes, f_living_room_thermostat.commands, f_events) == (
        {'living-room': ZoneCycleOutcome.THROTTLED},
        {'living-room': ZoneCycleOutcome.APPLIED},
        [('setpoint', Decimal('30.0'))],
        [_issued('living-room', '30.0', MORNING + timedelta(minutes=20))],
    )


@pytest.mark.parametrize(
    'f_expired_override_cycle',
    [NIGHT - MINUTE, NIGHT + timedelta(hours=2, minutes=1)],
    ids=['override_over', 'override_ending_beyond_two_hours_after_the_clock_moved_back'],
    indirect=True,
)
def test_expired_override_returns_the_floor_to_its_schedule(
    f_expired_override_cycle: RunHeatingCycleUseCase,
    f_living_room_thermostat: FloorThermostatStub,
) -> None:
    output = f_expired_override_cycle.execute(_at(NIGHT))

    assert (output.outcomes, f_living_room_thermostat.commands) == (
        {'living-room': ZoneCycleOutcome.APPLIED},
        [('setpoint', Decimal('22.0'))],
    )


@pytest.mark.parametrize(
    ('f_overridden_air_cycle', 'commands'),
    [('23.0', [('setpoint', Decimal('30.0'))]), (None, [])],
    ids=['override_above_the_air_heats', 'schedule_holds_idle'],
    indirect=['f_overridden_air_cycle'],
)
def test_live_air_zone_heats_to_an_override_target(
    f_overridden_air_cycle: RunHeatingCycleUseCase,
    f_living_room_thermostat: FloorThermostatStub,
    commands: list[tuple[str, Decimal]],
) -> None:
    f_overridden_air_cycle.execute(_at(MORNING))

    assert f_living_room_thermostat.commands == commands


def test_floor_mode_preheat_teaches_air_zone_its_morning(f_morning_cycle: RunHeatingCycleUseCase) -> None:
    f_morning_cycle.execute(_at(COMFORT_AT))

    learning = ZoneLearning.objects.get(zone__slug='kitchen')
    assert (learning.dead_time, learning.heat_up_rates, learning.mornings) == (
        timedelta(minutes=40),
        {'-5': '0.777'},
        1,
    )


def test_air_mode_morning_learns_its_rate_until_air_mode_stops_heating(
    f_air_mode_morning_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    f_air_mode_morning_cycle.execute(_at(AIR_MODE_STOP))

    learning = ZoneLearning.objects.get(zone__slug='kitchen')
    assert (f_thermostat.commands, learning.dead_time, learning.heat_up_rates, learning.mornings) == (
        [('setpoint', Decimal('30.0')), ('setpoint', Decimal('18.0'))],
        timedelta(minutes=40),
        {'-5': '0.910'},
        4,
    )


def test_preheat_whose_write_fails_teaches_no_morning(f_failed_preheat_cycle: RunHeatingCycleUseCase) -> None:
    f_failed_preheat_cycle.execute(_at(COMFORT_AT))

    assert not ZoneLearning.objects.exists()


def test_morning_is_learned_once(f_learned_morning_cycle: RunHeatingCycleUseCase) -> None:
    f_learned_morning_cycle.execute(_at(COMFORT_AT + MINUTE))

    assert ZoneLearning.objects.get(zone__slug='kitchen').mornings == 1


def test_stop_of_live_air_heat_teaches_early_stop_offset_from_two_hour_peak(
    f_watched_stop_cycle: RunHeatingCycleUseCase,
) -> None:
    f_watched_stop_cycle.execute(_at(MORNING + timedelta(hours=2, minutes=1)))

    assert ZoneLearning.objects.get(zone__slug='kitchen').early_stop_offset == Decimal('0.33')


def test_stop_is_learned_once(f_learned_stop_cycle: RunHeatingCycleUseCase) -> None:
    f_learned_stop_cycle.execute(_at(MORNING + timedelta(hours=2, minutes=2)))

    assert ZoneLearning.objects.get(zone__slug='kitchen').early_stop_offset == Decimal('0.33')


def test_stop_in_dry_run_teaches_nothing(f_dry_run_stop_cycle: RunHeatingCycleUseCase) -> None:
    f_dry_run_stop_cycle.execute(_at(MORNING + timedelta(hours=2, minutes=1)))

    assert not ZoneLearning.objects.exists()


@pytest.fixture
def f_living_room_thermostat() -> FloorThermostatStub:
    return FloorThermostatStub(state=None)


@pytest.fixture
def f_book() -> ControlBook:
    return ControlBook()


@pytest.fixture
def house_zones() -> list[ZoneSettings]:
    _backup_zone()
    _air_zone()
    _living_room()
    return ZoneRepository.all_settings()


@pytest.fixture
def house_thermostats(
    house_zones: list[ZoneSettings],
    store: ThermostatStateStore,
    f_thermostat: FloorThermostatStub,
    f_backup_thermostat: FloorThermostatStub,
    f_living_room_thermostat: FloorThermostatStub,
) -> dict[str, FloorThermostat]:
    for zone in house_zones:
        store.record_state(zone.thermostat_slug, _state('22.0'))
    return {BATHROOM: f_backup_thermostat, KITCHEN: f_thermostat, LIVING_ROOM: f_living_room_thermostat}


@pytest.fixture
def locked_learning() -> ZoneLearningStoreStub:
    return ZoneLearningStoreStub(records={}, errors={'kitchen': OperationalError(LOCKED)}, read_errors=())


@pytest.fixture
def f_finishing_morning_memory() -> ZoneMemory:
    return ZoneMemory(
        path=ControlPath.FLOOR,
        mode=ScheduleMode.PREHEAT,
        morning=Morning(
            comfort_at=COMFORT_AT,
            target=Decimal('22.0'),
            outdoor=Decimal('-3.4'),
            samples=tuple(AirSample(at=at, air=Decimal(air)) for at, air in MORNING_AIR[1:]),
        ),
        preheated_for=COMFORT_AT,
    )


@pytest.fixture
def f_unsaved_morning_cycle(
    house_thermostats: dict[str, FloorThermostat],
    locked_learning: ZoneLearningStoreStub,
    f_finishing_morning_memory: ZoneMemory,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    f_book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    f_book.remember_zone('kitchen', f_finishing_morning_memory)
    return _use_case(store, temperatures, house_thermostats, f_book, dispatcher, learning=locked_learning)


@pytest.fixture
def f_kitchen_deleted_while_the_cycle_runs(
    house_thermostats: dict[str, FloorThermostat],
    house_zones: list[ZoneSettings],
    f_finishing_morning_memory: ZoneMemory,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    f_book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    def delete_kitchen() -> None:
        Zone.objects.filter(slug='kitchen').delete()

    f_book.remember_zone('kitchen', f_finishing_morning_memory)
    zones = ConcurrentStampZoneStoreStub(zones=house_zones, meanwhile=delete_kitchen)
    return _use_case(store, temperatures, house_thermostats, f_book, dispatcher, zones=zones)


@pytest.fixture
def f_unsaved_stop_cycle(
    house_thermostats: dict[str, FloorThermostat],
    locked_learning: ZoneLearningStoreStub,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    f_book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    Zone.objects.filter(slug='kitchen').update(air_control_live=True)
    temperatures.record_state('air-kitchen', _reading('22.1'))
    f_book.remember_zone(
        'kitchen',
        ZoneMemory(
            path=ControlPath.AIR,
            mode=ScheduleMode.COMFORT,
            air_state=AirState.IDLE,
            heat_target=Decimal('22.0'),
            stop=StopWatch(stopped_at=MORNING, target=Decimal('22.0'), offset=Decimal('0.10'), peak=Decimal('22.3')),
        ),
    )
    return _use_case(store, temperatures, house_thermostats, f_book, dispatcher, learning=locked_learning)


@pytest.fixture
def f_locked_air_stamp_zones(house_zones: list[ZoneSettings]) -> ZoneStoreStub:
    return ZoneStoreStub(
        zones=house_zones,
        errors={('last_air_dry_run_at', 'kitchen'): OperationalError(LOCKED)},
    )


@pytest.fixture
def f_unstamped_air_dry_run_cycle(
    house_thermostats: dict[str, FloorThermostat],
    f_locked_air_stamp_zones: ZoneStoreStub,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    f_book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    store.record_state(KITCHEN, _state('28.0'))
    temperatures.record_state('air-kitchen', _reading('20.4'))
    return _use_case(store, temperatures, house_thermostats, f_book, dispatcher, zones=f_locked_air_stamp_zones)


@pytest.fixture
def f_air_dry_run_after_unstamped_change(
    house_thermostats: dict[str, FloorThermostat],
    f_locked_air_stamp_zones: ZoneStoreStub,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    f_book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    store.record_state(KITCHEN, _state('28.0'))
    temperatures.record_state('air-kitchen', _reading('20.4'))
    _use_case(store, temperatures, house_thermostats, f_book, dispatcher, zones=f_locked_air_stamp_zones).execute(
        _at(MORNING),
    )
    temperatures.record_state('air-kitchen', _reading('22.0'))
    return _use_case(store, temperatures, house_thermostats, f_book, dispatcher)


@pytest.fixture
def f_locked_command_stamp_zones(house_zones: list[ZoneSettings]) -> ZoneStoreStub:
    return ZoneStoreStub(
        zones=house_zones,
        errors={('last_command_at', 'kitchen'): OperationalError(LOCKED)},
    )


@pytest.fixture
def f_unstamped_command_cycle(
    house_thermostats: dict[str, FloorThermostat],
    f_locked_command_stamp_zones: ZoneStoreStub,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    f_book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    return _use_case(store, temperatures, house_thermostats, f_book, dispatcher, zones=f_locked_command_stamp_zones)


def test_morning_that_fails_to_save_is_lost_while_the_cycle_goes_on(
    f_unsaved_morning_cycle: RunHeatingCycleUseCase,
    f_living_room_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
    f_book: ControlBook,
) -> None:
    with capture_logs() as logs:
        output = f_unsaved_morning_cycle.execute(_at(COMFORT_AT))

    assert (
        output.outcomes,
        f_living_room_thermostat.commands,
        f_events,
        f_book.zone('kitchen'),
        [(log['event'], log['log_level'], log['zone'], log['error']) for log in logs],
    ) == (
        {
            'bathroom-2f': ZoneCycleOutcome.APPLIED,
            'kitchen': ZoneCycleOutcome.APPLIED,
            'living-room': ZoneCycleOutcome.APPLIED,
        },
        [('setpoint', Decimal('28.0'))],
        [
            _issued('bathroom-2f', '30.0', COMFORT_AT),
            _issued('kitchen', '28.0', COMFORT_AT),
            _issued('living-room', '28.0', COMFORT_AT),
        ],
        ZoneMemory(
            path=ControlPath.FLOOR,
            mode=ScheduleMode.COMFORT,
            would_be_target=ThermostatTarget(power=True, setpoint=Decimal('28.0')),
            preheated_for=COMFORT_AT,
        ),
        [('zone_morning_save_failed', 'warning', 'kitchen', LOCKED)],
    )


def test_stop_that_fails_to_save_is_lost_while_the_cycle_goes_on(
    f_unsaved_stop_cycle: RunHeatingCycleUseCase,
    f_living_room_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
    f_book: ControlBook,
) -> None:
    now = MORNING + timedelta(hours=2, minutes=1)

    with capture_logs() as logs:
        output = f_unsaved_stop_cycle.execute(_at(now))

    assert (
        output.outcomes,
        f_living_room_thermostat.commands,
        f_events,
        f_book.zone('kitchen'),
        [(log['event'], log['log_level'], log['zone'], log['error']) for log in logs],
    ) == (
        {
            'bathroom-2f': ZoneCycleOutcome.APPLIED,
            'kitchen': ZoneCycleOutcome.APPLIED,
            'living-room': ZoneCycleOutcome.APPLIED,
        },
        [('setpoint', Decimal('28.0'))],
        [_issued('bathroom-2f', '30.0', now), _issued('kitchen', '18.0', now), _issued('living-room', '28.0', now)],
        ZoneMemory(
            path=ControlPath.AIR,
            mode=ScheduleMode.COMFORT,
            air_state=AirState.IDLE,
            heat_target=Decimal('22.0'),
            would_be_target=ThermostatTarget(power=True, setpoint=Decimal('18.0')),
        ),
        [('zone_early_stop_offset_save_failed', 'warning', 'kitchen', LOCKED)],
    )


def test_zone_deleted_while_the_cycle_runs_reports_failed_while_the_cycle_goes_on(
    f_kitchen_deleted_while_the_cycle_runs: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_living_room_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
    f_book: ControlBook,
    f_finishing_morning_memory: ZoneMemory,
) -> None:
    with capture_logs() as logs:
        output = f_kitchen_deleted_while_the_cycle_runs.execute(_at(COMFORT_AT))

    assert (
        output.outcomes,
        f_thermostat.commands,
        f_living_room_thermostat.commands,
        f_events,
        f_book.zone('kitchen'),
        [(log['event'], log['log_level'], log['zone'], log['error']) for log in logs],
    ) == (
        {
            'bathroom-2f': ZoneCycleOutcome.APPLIED,
            'kitchen': ZoneCycleOutcome.FAILED,
            'living-room': ZoneCycleOutcome.APPLIED,
        },
        [('setpoint', Decimal('28.0'))],
        [('setpoint', Decimal('28.0'))],
        [
            _issued('bathroom-2f', '30.0', COMFORT_AT),
            _issued('kitchen', '28.0', COMFORT_AT),
            _issued('living-room', '28.0', COMFORT_AT),
        ],
        f_finishing_morning_memory,
        [('zone_cycle_failed', 'warning', 'kitchen', 'zone not found: kitchen')],
    )


def test_air_dry_run_change_whose_stamp_fails_is_held_while_the_cycle_goes_on(
    f_unstamped_air_dry_run_cycle: RunHeatingCycleUseCase,
    f_locked_air_stamp_zones: ZoneStoreStub,
    f_living_room_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
    f_book: ControlBook,
) -> None:
    with capture_logs() as logs:
        output = f_unstamped_air_dry_run_cycle.execute(_at(MORNING))

    assert (
        output.outcomes,
        f_living_room_thermostat.commands,
        f_locked_air_stamp_zones.stamps,
        f_events,
        f_book.zone('kitchen'),
        [(log['event'], log['log_level'], log['zone'], log['error']) for log in logs],
    ) == (
        {
            'bathroom-2f': ZoneCycleOutcome.APPLIED,
            'kitchen': ZoneCycleOutcome.UNCHANGED,
            'living-room': ZoneCycleOutcome.APPLIED,
        },
        [('setpoint', Decimal('28.0'))],
        [('last_command_at', 'bathroom-2f', MORNING), ('last_command_at', 'living-room', MORNING)],
        [_issued('bathroom-2f', '30.0', MORNING), _issued('living-room', '28.0', MORNING)],
        ZoneMemory(path=ControlPath.AIR_DRY_RUN, mode=ScheduleMode.COMFORT),
        [('zone_air_dry_run_stamp_failed', 'warning', 'kitchen', LOCKED)],
    )


def test_air_dry_run_change_whose_stamp_failed_holds_back_no_later_change(
    f_air_dry_run_after_unstamped_change: RunHeatingCycleUseCase,
    f_events: list[DomainEvent],
) -> None:
    now = MORNING + MINUTE

    f_air_dry_run_after_unstamped_change.execute(_at(now))

    assert (
        [event for event in f_events if isinstance(event, AirControlDryRun)],
        Zone.objects.get(slug='kitchen').last_air_dry_run_at,
    ) == (
        [
            AirControlDryRun(
                zone='kitchen',
                air=Decimal('22.0'),
                target=Decimal('22.0'),
                mode=ScheduleMode.COMFORT,
                state=AirState.IDLE,
                previous_state=None,
                power=True,
                setpoint=Decimal('18.0'),
                start_offset=Decimal('0.30'),
                early_stop_offset=Decimal('0.10'),
                lead_minutes=None,
                occurred_at=now,
            ),
        ],
        now,
    )


def test_zone_whose_write_stamp_fails_reports_failed_while_the_cycle_goes_on(
    f_unstamped_command_cycle: RunHeatingCycleUseCase,
    f_locked_command_stamp_zones: ZoneStoreStub,
    f_thermostat: FloorThermostatStub,
    f_living_room_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
    f_book: ControlBook,
) -> None:
    with capture_logs() as logs:
        output = f_unstamped_command_cycle.execute(_at(MORNING))

    assert (
        output.outcomes,
        f_thermostat.commands,
        f_living_room_thermostat.commands,
        f_locked_command_stamp_zones.stamps,
        f_events,
        f_book.zone('kitchen'),
        f_book.zone('living-room'),
        [(log['event'], log['log_level'], log['zone'], log['error']) for log in logs],
    ) == (
        {
            'bathroom-2f': ZoneCycleOutcome.APPLIED,
            'kitchen': ZoneCycleOutcome.FAILED,
            'living-room': ZoneCycleOutcome.APPLIED,
        },
        [],
        [('setpoint', Decimal('28.0'))],
        [('last_command_at', 'bathroom-2f', MORNING), ('last_command_at', 'living-room', MORNING)],
        [_issued('bathroom-2f', '30.0', MORNING), _issued('living-room', '28.0', MORNING)],
        ZoneMemory(),
        ZoneMemory(path=ControlPath.FLOOR, mode=ScheduleMode.COMFORT),
        [('zone_cycle_failed', 'warning', 'kitchen', LOCKED)],
    )


@pytest.fixture
def f_backup_after_unread_learning_cycle(
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_backup_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    _house_settings()
    store.record_state(BATHROOM, _state('22.0'))
    temperatures.record_state('air-second-floor', _reading('18.8'))
    thermostats: dict[str, FloorThermostat] = {BATHROOM: f_backup_thermostat}
    learning = ZoneLearningStoreStub(records={}, errors={}, read_errors=(OperationalError(LOCKED),))
    with pytest.raises(OperationalError):
        _use_case(store, temperatures, thermostats, book, EventDispatcher(), learning=learning).execute(_at(NIGHT))
    return _use_case(store, temperatures, thermostats, book, dispatcher, learning=learning)


def test_backup_heat_change_of_a_cycle_that_fails_to_read_learning_is_journaled_by_the_next_one(
    f_backup_after_unread_learning_cycle: RunHeatingCycleUseCase,
    f_events: list[DomainEvent],
) -> None:
    now = NIGHT + MINUTE

    f_backup_after_unread_learning_cycle.execute(_at(now))

    assert f_events == [
        BackupHeatDryRun(
            room='bedroom',
            room_name='Спальня',
            zone='bathroom-2f',
            active=True,
            air=Decimal('18.8'),
            setpoint=Decimal('30.0'),
            occurred_at=now,
        ),
    ]


@pytest.fixture
def capped_lead_thermostats(
    temperatures: TemperatureStateStore,
    f_thermostat: FloorThermostatStub,
) -> dict[str, FloorThermostat]:
    _learned(_air_zone(air_control_live=True), rate='0.500')
    ClimateSettingsFactory.create(outdoor_sensor=_sensor('outdoor'))
    _read(temperatures, {'air-kitchen': '19.4', 'outdoor': '-3.4'})
    return {KITCHEN: f_thermostat}


@pytest.fixture
def f_air_sensor_flap_cycle(
    capped_lead_thermostats: dict[str, FloorThermostat],
    clock: MonotonicClockStub,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    f_book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    store.record_state(KITCHEN, _state('18.0'))
    for at in (CAPPED_START - MINUTE, CAPPED_START):
        _use_case(store, temperatures, capped_lead_thermostats, f_book, EventDispatcher()).execute(_at(at))
    clock.advance(STALE.total_seconds())
    store.record_state(KITCHEN, _state('30.0'))
    temperatures.record_state('outdoor', _reading('-3.4'))
    _use_case(store, temperatures, capped_lead_thermostats, f_book, EventDispatcher()).execute(
        _at(CAPPED_START + timedelta(minutes=15)),
    )
    temperatures.record_state('air-kitchen', _reading('19.5'))
    return _use_case(store, temperatures, capped_lead_thermostats, f_book, dispatcher)


@pytest.fixture
def f_air_sensor_flap_after_held_back_preheat_cycle(
    capped_lead_thermostats: dict[str, FloorThermostat],
    clock: MonotonicClockStub,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    f_book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    Zone.objects.filter(slug='kitchen').update(last_command_at=CAPPED_START - timedelta(minutes=20))
    store.record_state(KITCHEN, _state('18.0'))
    for at in (CAPPED_START - MINUTE, CAPPED_START, CAPPED_START + timedelta(minutes=10)):
        _use_case(store, temperatures, capped_lead_thermostats, f_book, EventDispatcher()).execute(_at(at))
    clock.advance(STALE.total_seconds())
    store.record_state(KITCHEN, _state('30.0'))
    temperatures.record_state('outdoor', _reading('-3.4'))
    _use_case(store, temperatures, capped_lead_thermostats, f_book, EventDispatcher()).execute(
        _at(CAPPED_START + timedelta(minutes=15)),
    )
    temperatures.record_state('air-kitchen', _reading('19.5'))
    return _use_case(store, temperatures, capped_lead_thermostats, f_book, dispatcher)


@pytest.fixture
def f_air_sensor_lost_at_preheat_start_cycle(
    capped_lead_thermostats: dict[str, FloorThermostat],
    clock: MonotonicClockStub,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    f_book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    store.record_state(KITCHEN, _state('22.0'))
    _use_case(store, temperatures, capped_lead_thermostats, f_book, EventDispatcher()).execute(
        _at(CAPPED_START - timedelta(minutes=15)),
    )
    clock.advance(STALE.total_seconds())
    store.record_state(KITCHEN, _state('18.0'))
    temperatures.record_state('outdoor', _reading('-3.4'))
    _use_case(store, temperatures, capped_lead_thermostats, f_book, EventDispatcher()).execute(_at(CAPPED_START))
    temperatures.record_state('air-kitchen', _reading('19.4'))
    return _use_case(store, temperatures, capped_lead_thermostats, f_book, dispatcher)


@pytest.fixture
def f_restart_in_air_mode_preheat_cycle(
    request: pytest.FixtureRequest,
    capped_lead_thermostats: dict[str, FloorThermostat],
    clock: MonotonicClockStub,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    f_book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    polled_before_first_cycle = request.param
    Zone.objects.filter(slug='kitchen').update(last_command_at=CAPPED_START)
    clock.advance(STALE.total_seconds())
    temperatures.record_state('outdoor', _reading('-3.4'))
    if polled_before_first_cycle:
        store.record_state(KITCHEN, _state('30.0'))
    _use_case(store, temperatures, capped_lead_thermostats, f_book, EventDispatcher()).execute(
        _at(CAPPED_START + timedelta(minutes=10)),
    )
    store.record_state(KITCHEN, _state('30.0'))
    temperatures.record_state('air-kitchen', _reading('19.4'))
    return _use_case(store, temperatures, capped_lead_thermostats, f_book, dispatcher)


@pytest.fixture
def f_untaken_night_cycle(
    capped_lead_thermostats: dict[str, FloorThermostat],
    clock: MonotonicClockStub,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    f_book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    clock.advance(STALE.total_seconds())
    store.record_state(KITCHEN, _state('22.0'))
    _use_case(store, temperatures, capped_lead_thermostats, f_book, EventDispatcher()).execute(
        _at(CAPPED_START - timedelta(minutes=20), clock_trusted=False),
    )
    clock.advance(STALE.total_seconds())
    _use_case(store, temperatures, capped_lead_thermostats, f_book, EventDispatcher()).execute(
        _at(CAPPED_START + timedelta(minutes=14)),
    )
    store.record_state(KITCHEN, _state('28.0'))
    _read(temperatures, {'air-kitchen': '19.4', 'outdoor': '-3.4'})
    return _use_case(store, temperatures, capped_lead_thermostats, f_book, dispatcher)


def test_air_sensor_back_in_preheat_starts_no_second_morning_for_its_comfort_start(
    f_air_sensor_flap_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_book: ControlBook,
) -> None:
    output = f_air_sensor_flap_cycle.execute(_at(CAPPED_START + timedelta(minutes=16)))

    assert (output.outcomes, f_thermostat.commands, f_book.zone('kitchen').morning) == (
        {'kitchen': ZoneCycleOutcome.UNCHANGED},
        [('setpoint', Decimal('30.0'))],
        None,
    )


def test_air_sensor_back_in_preheat_starts_no_morning_once_its_held_back_write_went_out(
    f_air_sensor_flap_after_held_back_preheat_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_book: ControlBook,
) -> None:
    output = f_air_sensor_flap_after_held_back_preheat_cycle.execute(_at(CAPPED_START + timedelta(minutes=16)))

    assert (output.outcomes, f_thermostat.commands, f_book.zone('kitchen').morning) == (
        {'kitchen': ZoneCycleOutcome.UNCHANGED},
        [('setpoint', Decimal('30.0'))],
        None,
    )


@pytest.mark.parametrize(
    'f_restart_in_air_mode_preheat_cycle',
    [True, False],
    ids=['night_write_waits_its_turn', 'thermostat_not_polled_yet'],
    indirect=True,
)
def test_restart_in_air_mode_preheat_starts_no_morning_when_the_air_sensor_first_reports(
    f_restart_in_air_mode_preheat_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_book: ControlBook,
) -> None:
    output = f_restart_in_air_mode_preheat_cycle.execute(_at(CAPPED_START + timedelta(minutes=11)))

    assert (output.outcomes, f_thermostat.commands, f_book.zone('kitchen').morning) == (
        {'kitchen': ZoneCycleOutcome.UNCHANGED},
        [],
        None,
    )


def test_air_sensor_back_in_preheat_starts_morning_missed_at_its_start(
    f_air_sensor_lost_at_preheat_start_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_book: ControlBook,
) -> None:
    now = CAPPED_START + timedelta(minutes=16)

    output = f_air_sensor_lost_at_preheat_start_cycle.execute(_at(now))

    assert (output.outcomes, f_thermostat.commands, f_book.zone('kitchen').morning) == (
        {'kitchen': ZoneCycleOutcome.APPLIED},
        [('setpoint', Decimal('18.0')), ('setpoint', Decimal('30.0'))],
        Morning(
            comfort_at=COMFORT_AT,
            target=Decimal('22.0'),
            outdoor=Decimal('-3.4'),
            samples=(AirSample(at=now, air=Decimal('19.4')),),
        ),
    )


def test_air_sensor_back_in_preheat_after_a_night_its_thermostat_never_took_starts_no_morning(
    f_untaken_night_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_book: ControlBook,
) -> None:
    output = f_untaken_night_cycle.execute(_at(CAPPED_START + timedelta(minutes=16)))

    assert (
        output.outcomes,
        f_thermostat.commands,
        f_book.zone('kitchen').morning,
        f_book.zone('kitchen').preheated_for,
    ) == (
        {'kitchen': ZoneCycleOutcome.APPLIED},
        [('setpoint', Decimal('28.0')), ('setpoint', Decimal('30.0'))],
        None,
        COMFORT_AT,
    )


@pytest.fixture
def f_held_gate() -> LoadGateStub:
    return LoadGateStub(held=frozenset({'kitchen', 'bathroom-2f'}), gated=frozenset(), answers={})


@pytest.fixture
def f_guard_book() -> GuardBook:
    return GuardBook()


@pytest.fixture
def f_gate_clock() -> MonotonicClockStub:
    return MonotonicClockStub(now=0.0)


@pytest.fixture
def load_gate(f_guard_book: GuardBook, f_gate_clock: MonotonicClockStub) -> GuardLoadGate:
    return GuardLoadGate(book=f_guard_book, clock=f_gate_clock)


@pytest.fixture
def f_held_cycle(
    request: pytest.FixtureRequest,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
    f_backup_thermostat: FloorThermostatStub,
    f_held_gate: LoadGateStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    house: House = request.param
    house(store, temperatures)
    thermostats: dict[str, FloorThermostat] = {KITCHEN: f_thermostat, BATHROOM: f_backup_thermostat}
    return _use_case(store, temperatures, thermostats, book, dispatcher, gate=f_held_gate)


@pytest.fixture
def f_restored_cycle(f_thermostat: FloorThermostatStub, dispatcher: EventDispatcher) -> RunHeatingCycleUseCase:
    zone = _floor_zone('kitchen', KITCHEN, last_command_at=MORNING - timedelta(minutes=10))
    return _cycle(zone, _state('22.0', power=False), f_thermostat, dispatcher)


@pytest.fixture
def f_load_management_off_cycle(
    request: pytest.FixtureRequest,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
    f_guard_book: GuardBook,
    load_gate: GuardLoadGate,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    house, management_live, load_live, meter, l2 = request.param
    house(store, temperatures)
    _watch(f_guard_book, management_live=management_live, load_live=load_live, meter=meter, l2=l2)
    return _use_case(store, temperatures, {KITCHEN: f_thermostat}, book, dispatcher, gate=load_gate)


@pytest.fixture
def f_live_start_cycle(
    request: pytest.FixtureRequest,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
    f_guard_book: GuardBook,
    load_gate: GuardLoadGate,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    meter, l2, readings = request.param
    _idle_floor_zone(store, temperatures)
    _watch(f_guard_book, management_live=True, meter=meter, l2=l2, readings=readings)
    return _use_case(store, temperatures, {KITCHEN: f_thermostat}, book, dispatcher, gate=load_gate)


@pytest.fixture
def f_two_starts_cycle(
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
    f_living_room_thermostat: FloorThermostatStub,
    f_guard_book: GuardBook,
    load_gate: GuardLoadGate,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    _idle_floor_zone(store, temperatures)
    _floor_zone('living-room', LIVING_ROOM)
    store.record_state(LIVING_ROOM, _state('22.0', heating=False))
    _watch(f_guard_book, management_live=True)
    thermostats: dict[str, FloorThermostat] = {KITCHEN: f_thermostat, LIVING_ROOM: f_living_room_thermostat}
    return _use_case(store, temperatures, thermostats, book, dispatcher, gate=load_gate)


@pytest.fixture
def f_two_starts_after_a_restart_that_took_back_the_hold_of_the_living_room(
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
    f_living_room_thermostat: FloorThermostatStub,
    f_guard_book: GuardBook,
    load_gate: GuardLoadGate,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    _idle_floor_zone(store, temperatures)
    _floor_zone('living-room', LIVING_ROOM, last_command_at=MORNING - timedelta(minutes=5))
    store.record_state(LIVING_ROOM, _state('22.0', power=False))
    _watch(f_guard_book, management_live=True)
    writes = _write_gate(MonotonicClockStub(now=0.0))
    RecoverWriteHoldsUseCase(
        loads=LoadStoreStub(records=[LIVING_ROOM_HANDED_BACK_BEFORE_A_RESTART], live_zones=frozenset({'living-room'})),
        writes=writes,
        source_writes=_write_gate(MonotonicClockStub(now=0.0)),
        dry_run=False,
    ).execute(RecoverWriteHoldsInputDTO(now=MORNING))
    thermostats: dict[str, FloorThermostat] = {KITCHEN: f_thermostat, LIVING_ROOM: f_living_room_thermostat}
    return _use_case(store, temperatures, thermostats, book, dispatcher, gate=load_gate, writes=writes)


@pytest.mark.parametrize(
    ('f_held_cycle', 'now', 'zone', 'commands'),
    [
        (_shed_floor_zone, MORNING, 'kitchen', [('power', True), ('setpoint', Decimal('28.0'))]),
        (_shed_air_zone, MORNING, 'kitchen', [('power', True), ('setpoint', Decimal('30.0'))]),
        (_shed_boosted_zone, MORNING, 'kitchen', [('power', True), ('setpoint', Decimal('30.0'))]),
        (_shed_backed_up_zone, NIGHT, 'bathroom-2f', [('power', True), ('setpoint', Decimal('30.0'))]),
    ],
    ids=['floor_mode', 'air_mode', 'boost', 'backup_heat'],
    indirect=['f_held_cycle'],
)
def test_shed_zone_is_held_on_every_path_and_gets_its_target_once_restored(
    f_held_cycle: RunHeatingCycleUseCase,
    f_held_gate: LoadGateStub,
    f_thermostat: FloorThermostatStub,
    f_backup_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
    now: datetime,
    zone: str,
    commands: list[tuple[str, object]],
) -> None:
    held = f_held_cycle.execute(_at(now))
    held_writes = (f_thermostat.commands + f_backup_thermostat.commands, list(f_events))
    held_stamp = Zone.objects.get(slug=zone).last_command_at
    f_held_gate.release(zone)
    restored = f_held_cycle.execute(_at(now))

    assert (
        held.outcomes,
        held_writes,
        held_stamp,
        restored.outcomes,
        f_thermostat.commands + f_backup_thermostat.commands,
        Zone.objects.get(slug=zone).last_command_at,
        f_held_gate.asked,
    ) == (
        {zone: ZoneCycleOutcome.SHED},
        ([], []),
        now - HALF_HOUR,
        {zone: ZoneCycleOutcome.APPLIED},
        commands,
        now,
        [(), (ZoneStart(zone=zone, writes_live=True),)],
    )


def test_restored_zone_gets_its_target_no_sooner_than_thirty_minutes_after_its_shed(
    f_restored_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    output = f_restored_cycle.execute(_at(MORNING))

    assert (output.outcomes, f_thermostat.commands) == ({'kitchen': ZoneCycleOutcome.THROTTLED}, [])


@pytest.mark.parametrize(
    ('f_load_management_off_cycle', 'setpoint'),
    [
        ((_idle_floor_zone, False, False, FRESH_METER, '13.0'), Decimal('28.0')),
        ((_idle_air_zone, False, False, FRESH_METER, '13.0'), Decimal('30.0')),
        ((_idle_boosted_zone, False, False, FRESH_METER, '13.0'), Decimal('30.0')),
        ((_idle_floor_zone, True, False, FRESH_METER, '13.0'), Decimal('28.0')),
        ((_idle_floor_zone, False, True, FRESH_METER, '13.0'), Decimal('28.0')),
    ],
    ids=['floor_mode', 'air_mode', 'boost', 'load_off', 'load_management_off'],
    indirect=['f_load_management_off_cycle'],
)
def test_start_that_would_wait_with_load_management_off_is_written_as_before_and_journaled(
    f_load_management_off_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_guard_book: GuardBook,
    f_events: list[DomainEvent],
    setpoint: Decimal,
) -> None:
    output = f_load_management_off_cycle.execute(_at(MORNING))

    zone = Zone.objects.get(slug='kitchen')
    assert (
        output.outcomes,
        f_thermostat.commands,
        zone.last_command_at,
        zone.last_dry_run_at,
        f_events,
        f_guard_book.last_permit_at(),
    ) == (
        {'kitchen': ZoneCycleOutcome.APPLIED},
        [('setpoint', setpoint)],
        MORNING,
        None,
        [
            StartWouldWaitDryRun(
                load='kitchen',
                name='Кухня',
                phase=LoadPhase.L2,
                amps=Decimal('21.0'),
                load_amps=Decimal('8.0'),
                reason=StartRefusal.NO_HEADROOM,
                occurred_at=MORNING,
            ),
            ZoneCommandIssued(
                zone='kitchen',
                change=ThermostatChange(setpoint=setpoint),
                dry_run=False,
                occurred_at=MORNING,
            ),
        ],
        None,
    )


@pytest.mark.parametrize(
    ('f_load_management_off_cycle', 'setpoint'),
    [
        ((_idle_floor_zone, False, False, FRESH_METER, '10.0'), Decimal('28.0')),
        ((_idle_air_zone, False, False, FRESH_METER, '10.0'), Decimal('30.0')),
        ((_idle_boosted_zone, False, False, FRESH_METER, '10.0'), Decimal('30.0')),
        ((_idle_floor_zone, False, False, UNSEEN_METER, '10.0'), Decimal('28.0')),
        ((_idle_air_zone, False, False, UNSEEN_METER, '10.0'), Decimal('30.0')),
        ((_idle_boosted_zone, False, False, UNSEEN_METER, '10.0'), Decimal('30.0')),
    ],
    ids=[
        'floor_mode_with_room',
        'air_mode_with_room',
        'boost_with_room',
        'floor_mode_before_the_meter_reports',
        'air_mode_before_the_meter_reports',
        'boost_before_the_meter_reports',
    ],
    indirect=['f_load_management_off_cycle'],
)
def test_cycle_with_load_management_off_writes_stamps_and_journals_exactly_as_before(
    f_load_management_off_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_guard_book: GuardBook,
    f_events: list[DomainEvent],
    setpoint: Decimal,
) -> None:
    output = f_load_management_off_cycle.execute(_at(MORNING))

    zone = Zone.objects.get(slug='kitchen')
    assert (
        output.outcomes,
        f_thermostat.commands,
        zone.last_command_at,
        zone.last_dry_run_at,
        zone.last_air_dry_run_at,
        f_events,
        f_guard_book.last_permit_at(),
    ) == (
        {'kitchen': ZoneCycleOutcome.APPLIED},
        [('setpoint', setpoint)],
        MORNING,
        None,
        None,
        [
            ZoneCommandIssued(
                zone='kitchen',
                change=ThermostatChange(setpoint=setpoint),
                dry_run=False,
                occurred_at=MORNING,
            ),
        ],
        None,
    )


@pytest.mark.parametrize(
    ('f_live_start_cycle', 'outcome', 'commands', 'stamp', 'last_permit_at'),
    [
        ((FRESH_METER, '12.0', None), ZoneCycleOutcome.APPLIED, [('setpoint', Decimal('28.0'))], MORNING, 0.0),
        ((FRESH_METER, '12.5', None), ZoneCycleOutcome.WAITING, [], None, None),
    ],
    ids=['permit_granted', 'permit_refused'],
    indirect=['f_live_start_cycle'],
)
def test_live_start_is_written_only_with_a_permit_and_keeps_the_write_budget_while_it_waits(
    f_live_start_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_guard_book: GuardBook,
    f_events: list[DomainEvent],
    outcome: ZoneCycleOutcome,
    commands: list[tuple[str, Decimal]],
    stamp: datetime | None,
    last_permit_at: float | None,
) -> None:
    output = f_live_start_cycle.execute(_at(MORNING))

    assert (
        output.outcomes,
        f_thermostat.commands,
        Zone.objects.get(slug='kitchen').last_command_at,
        f_guard_book.last_permit_at(),
        f_events,
    ) == (
        {'kitchen': outcome},
        commands,
        stamp,
        last_permit_at,
        [
            ZoneCommandIssued(
                zone='kitchen',
                change=ThermostatChange(setpoint=setpoint),
                dry_run=False,
                occurred_at=MORNING,
            )
            for _, setpoint in commands
        ],
    )


@pytest.mark.parametrize('f_live_start_cycle', [(FRESH_METER, '12.5', None)], indirect=True)
def test_waiting_start_asks_again_at_the_next_cycle(
    f_live_start_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_guard_book: GuardBook,
) -> None:
    waiting = f_live_start_cycle.execute(_at(MORNING))
    _watch(f_guard_book, management_live=True, l2='12.0')
    started = f_live_start_cycle.execute(_at(MORNING + MINUTE))

    assert (waiting.outcomes, started.outcomes, f_thermostat.commands) == (
        {'kitchen': ZoneCycleOutcome.WAITING},
        {'kitchen': ZoneCycleOutcome.APPLIED},
        [('setpoint', Decimal('28.0'))],
    )


def test_live_starts_of_one_cycle_go_most_important_first_one_a_cycle(
    f_two_starts_cycle: RunHeatingCycleUseCase,
    f_gate_clock: MonotonicClockStub,
    f_thermostat: FloorThermostatStub,
    f_living_room_thermostat: FloorThermostatStub,
) -> None:
    first = f_two_starts_cycle.execute(_at(MORNING))
    f_gate_clock.advance(MINUTE.total_seconds())
    second = f_two_starts_cycle.execute(_at(MORNING + MINUTE))

    assert (first.outcomes, second.outcomes, f_living_room_thermostat.commands, f_thermostat.commands) == (
        {'kitchen': ZoneCycleOutcome.WAITING, 'living-room': ZoneCycleOutcome.APPLIED},
        {'kitchen': ZoneCycleOutcome.APPLIED, 'living-room': ZoneCycleOutcome.THROTTLED},
        [('setpoint', Decimal('28.0'))],
        [('setpoint', Decimal('28.0'))],
    )


def test_floor_the_write_gate_holds_asks_for_no_start_so_the_next_floor_starts_in_the_same_cycle(
    f_two_starts_after_a_restart_that_took_back_the_hold_of_the_living_room: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_living_room_thermostat: FloorThermostatStub,
    f_guard_book: GuardBook,
) -> None:
    output = f_two_starts_after_a_restart_that_took_back_the_hold_of_the_living_room.execute(_at(STEPPED_FORWARD))

    assert (
        output.outcomes,
        f_thermostat.commands,
        f_living_room_thermostat.commands,
        f_guard_book.last_permit_at(),
        f_guard_book.waiting(),
    ) == (
        {'kitchen': ZoneCycleOutcome.APPLIED, 'living-room': ZoneCycleOutcome.THROTTLED},
        [('setpoint', Decimal('28.0'))],
        [],
        0.0,
        frozenset(),
    )


@pytest.mark.parametrize(
    ('f_live_start_cycle', 'outcome', 'commands'),
    [
        ((MeterWatch(seen=True, stale_since=-59.0), '10.0', None), ZoneCycleOutcome.WAITING, []),
        ((MeterWatch(seen=False, stale_since=-59.0), '10.0', None), ZoneCycleOutcome.WAITING, []),
        (
            (MeterWatch(seen=True, stale_since=-60.0), '10.0', None),
            ZoneCycleOutcome.APPLIED,
            [('setpoint', Decimal('28.0'))],
        ),
        (
            (MeterWatch(seen=False, stale_since=-60.0), '10.0', None),
            ZoneCycleOutcome.APPLIED,
            [('setpoint', Decimal('28.0'))],
        ),
        (
            (MeterWatch(seen=True, stale_since=-60.0), '10.0', {'hallway': HALLWAY_HEATING}),
            ZoneCycleOutcome.WAITING,
            [],
        ),
    ],
    ids=[
        'first_minute_of_a_stale_meter',
        'first_minute_after_the_start_without_a_meter_reading',
        'lost_meter_with_no_floor_drawing_on_the_phase',
        'meter_silent_since_the_start_for_a_minute',
        'lost_meter_with_another_floor_drawing_on_the_phase',
    ],
    indirect=['f_live_start_cycle'],
)
def test_live_start_on_a_stale_meter_waits_a_minute_then_keeps_one_drawing_floor_a_phase(
    f_live_start_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    outcome: ZoneCycleOutcome,
    commands: list[tuple[str, object]],
) -> None:
    output = f_live_start_cycle.execute(_at(MORNING))

    assert (output.outcomes, f_thermostat.commands) == ({'kitchen': outcome}, commands)


@pytest.fixture
def f_load_management_off_on_a_silent_overloaded_phase_cycle(
    request: pytest.FixtureRequest,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
    f_guard_book: GuardBook,
    load_gate: GuardLoadGate,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    house: House = request.param
    house(store, temperatures)
    _watch(
        f_guard_book,
        management_live=False,
        load_live=False,
        meter=LOST_METER,
        overloaded=frozenset({LoadPhase.L2}),
    )
    return _use_case(store, temperatures, {KITCHEN: f_thermostat}, book, dispatcher, gate=load_gate)


@pytest.fixture
def f_kitchen_shed_while_the_third_clamp_is_silent(
    clock: MonotonicClockStub,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
    f_guard_book: GuardBook,
    dispatcher: EventDispatcher,
) -> GuardedHouse:
    ZoneLoadFactory.create(
        slug='kitchen',
        zone=_floor_zone('kitchen', KITCHEN),
        priority=5,
        phase=LoadPhase.L2,
        nominal_amps=Decimal('8.0'),
        live=True,
    )
    LoadSettingsFactory.create(load_management_live=True)
    DeviceFactory.create(slug=GUARD_METER, kind=DeviceKind.PHASE_METER, driver=DeviceDriver.ESPHOME_PHASE_METER)
    store.record_state(KITCHEN, _state('28.0', heating=True))
    meter = PhaseMeterStore(stale_after=timedelta(seconds=10), unavailable_after=UNAVAILABLE_AFTER, clock=clock)
    writes = _write_gate(clock)
    zone_switch = ThermostatZoneSwitch(
        stamps=ZoneRepository(),
        thermostats=ThermostatProviderStub(thermostats={KITCHEN: f_thermostat}),
        writes=writes,
    )
    house = GuardedHouse(
        clock=clock,
        meter=meter,
        store=store,
        watch=WatchPhasesUseCase(
            loads=LoadRepository(),
            load_settings=LoadSettingsRepository(),
            devices=DeviceRepository(),
            meter=meter,
            thermostats=store,
            plugs=PlugStateStore(unavailable_after=UNAVAILABLE_AFTER, clock=clock),
            climate_units=ClimateUnitStateStore(unavailable_after=UNAVAILABLE_AFTER, clock=clock),
            book=f_guard_book,
            clock=clock,
            dispatcher=EventDispatcher(),
        ),
        shed=ShedPhaseLoadsUseCase(
            loads=LoadRepository(),
            switcher=LoadSwitcher(
                plugs=SwitchablePlugProviderStub(plugs={}),
                zones=zone_switch,
                sources=SourceSwitchStub(writes=_write_gate(clock), error=None),
            ),
            book=f_guard_book,
            clock=clock,
            dry_run=False,
            dispatcher=EventDispatcher(),
        ),
        cycle=_use_case(
            store,
            temperatures,
            {KITCHEN: f_thermostat},
            book,
            dispatcher,
            gate=GuardLoadGate(book=f_guard_book, clock=clock),
            writes=writes,
        ),
    )
    _guard_seconds(house, 65, '10.0')
    _guard_seconds(house, 4, '33.0')
    _poll_kitchen_off(house)
    return house


@pytest.fixture
def f_locked_start_zones(store: ThermostatStateStore, temperatures: TemperatureStateStore) -> ZoneStoreStub:
    _idle_floor_zone(store, temperatures)
    _floor_zone('living-room', LIVING_ROOM)
    store.record_state(LIVING_ROOM, _state('22.0'))
    return ZoneStoreStub(
        zones=ZoneRepository.all_settings(),
        errors={('last_command_at', 'kitchen'): OperationalError(LOCKED)},
    )


@pytest.fixture
def f_unstamped_start_cycle(
    f_locked_start_zones: ZoneStoreStub,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
    f_living_room_thermostat: FloorThermostatStub,
    f_guard_book: GuardBook,
    load_gate: GuardLoadGate,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    _watch(f_guard_book, management_live=False, load_live=False, l2='13.0')
    thermostats: dict[str, FloorThermostat] = {KITCHEN: f_thermostat, LIVING_ROOM: f_living_room_thermostat}
    return _use_case(
        store,
        temperatures,
        thermostats,
        book,
        dispatcher,
        zones=f_locked_start_zones,
        gate=load_gate,
    )


@pytest.mark.parametrize(
    ('f_load_management_off_on_a_silent_overloaded_phase_cycle', 'setpoint'),
    [(_idle_floor_zone, Decimal('28.0')), (_idle_air_zone, Decimal('30.0')), (_idle_boosted_zone, Decimal('30.0'))],
    ids=['floor_mode', 'air_mode', 'boost'],
    indirect=['f_load_management_off_on_a_silent_overloaded_phase_cycle'],
)
def test_start_on_a_phase_that_went_silent_overloaded_with_load_management_off_is_written_as_before_and_journaled(
    f_load_management_off_on_a_silent_overloaded_phase_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_guard_book: GuardBook,
    f_events: list[DomainEvent],
    setpoint: Decimal,
) -> None:
    output = f_load_management_off_on_a_silent_overloaded_phase_cycle.execute(_at(MORNING))

    zone = Zone.objects.get(slug='kitchen')
    assert (
        output.outcomes,
        f_thermostat.commands,
        zone.last_command_at,
        zone.last_dry_run_at,
        f_events,
        f_guard_book.last_permit_at(),
    ) == (
        {'kitchen': ZoneCycleOutcome.APPLIED},
        [('setpoint', setpoint)],
        MORNING,
        None,
        [
            StartWouldWaitDryRun(
                load='kitchen',
                name='Кухня',
                phase=LoadPhase.L2,
                amps=None,
                load_amps=Decimal('8.0'),
                reason=StartRefusal.PHASE_OVERLOADED,
                occurred_at=MORNING,
            ),
            ZoneCommandIssued(
                zone='kitchen',
                change=ThermostatChange(setpoint=setpoint),
                dry_run=False,
                occurred_at=MORNING,
            ),
        ],
        None,
    )


@pytest.mark.parametrize(
    ('l2', 'outcome', 'commands'),
    [
        ('30.0', ZoneCycleOutcome.WAITING, [('power', False)]),
        ('22.0', ZoneCycleOutcome.WAITING, [('power', False)]),
        ('15.0', ZoneCycleOutcome.APPLIED, [('power', False), ('power', True)]),
    ],
    ids=[
        'phase_overloaded_again_when_it_went_silent',
        'phase_above_the_restore_threshold_when_it_went_silent',
        'phase_calm_when_it_went_silent',
    ],
)
def test_floor_restored_on_a_lost_meter_is_started_by_the_cycle_only_if_its_phase_allows_it_by_then(
    f_kitchen_shed_while_the_third_clamp_is_silent: GuardedHouse,
    f_thermostat: FloorThermostatStub,
    l2: str,
    outcome: ZoneCycleOutcome,
    commands: list[tuple[str, object]],
) -> None:
    calm = _guard_seconds(f_kitchen_shed_while_the_third_clamp_is_silent, 125, '12.0')
    throttled = _guarded_cycle(f_kitchen_shed_while_the_third_clamp_is_silent)
    _guard_seconds(f_kitchen_shed_while_the_third_clamp_is_silent, 10, l2)
    _guard_seconds(f_kitchen_shed_while_the_third_clamp_is_silent, 15, None)
    f_kitchen_shed_while_the_third_clamp_is_silent.clock.advance(1700)
    _poll_kitchen_off(f_kitchen_shed_while_the_third_clamp_is_silent)
    _guard_seconds(f_kitchen_shed_while_the_third_clamp_is_silent, 1, None)
    started = _guarded_cycle(f_kitchen_shed_while_the_third_clamp_is_silent)

    assert (
        [output.restored for output in calm if output.restored],
        throttled.outcomes,
        started.outcomes,
        f_thermostat.commands,
    ) == ([('kitchen',)], {'kitchen': ZoneCycleOutcome.THROTTLED}, {'kitchen': outcome}, commands)


def test_start_that_would_wait_is_journaled_when_its_zone_then_fails_on_the_database(
    f_unstamped_start_cycle: RunHeatingCycleUseCase,
    f_locked_start_zones: ZoneStoreStub,
    f_thermostat: FloorThermostatStub,
    f_living_room_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
) -> None:
    with capture_logs() as logs:
        output = f_unstamped_start_cycle.execute(_at(MORNING))

    assert (
        output.outcomes,
        f_thermostat.commands,
        f_living_room_thermostat.commands,
        f_locked_start_zones.stamps,
        f_events,
        logs,
    ) == (
        {'kitchen': ZoneCycleOutcome.FAILED, 'living-room': ZoneCycleOutcome.APPLIED},
        [],
        [('setpoint', Decimal('28.0'))],
        [('last_command_at', 'living-room', MORNING)],
        [
            StartWouldWaitDryRun(
                load='kitchen',
                name='Кухня',
                phase=LoadPhase.L2,
                amps=Decimal('21.0'),
                load_amps=Decimal('8.0'),
                reason=StartRefusal.NO_HEADROOM,
                occurred_at=MORNING,
            ),
            _issued('living-room', '28.0', MORNING),
        ],
        [
            {
                'event': 'load_start_waits',
                'log_level': 'info',
                'load': 'kitchen',
                'live': False,
                'reason': StartRefusal.NO_HEADROOM,
            },
            {'event': 'zone_cycle_failed', 'log_level': 'warning', 'zone': 'kitchen', 'error': LOCKED},
        ],
    )


@pytest.fixture
def f_kitchen_written_while_living_room_is_polled(
    request: pytest.FixtureRequest,
    store: ThermostatStateStore,
) -> ConcurrentWriteThermostatStub:
    polled: ThermostatState = request.param
    return ConcurrentWriteThermostatStub(meanwhile=lambda: store.record_state(LIVING_ROOM, polled))


@pytest.fixture
def f_poll_between_writes_cycle(
    request: pytest.FixtureRequest,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_kitchen_written_while_living_room_is_polled: ConcurrentWriteThermostatStub,
    f_living_room_thermostat: FloorThermostatStub,
    f_guard_book: GuardBook,
    load_gate: GuardLoadGate,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    management_live, cached, l2 = request.param
    _floor_zone('kitchen', KITCHEN)
    _floor_zone('living-room', LIVING_ROOM)
    store.record_state(KITCHEN, _state('22.0'))
    store.record_state(LIVING_ROOM, cached)
    _watch(f_guard_book, management_live=management_live, load_live=management_live, l2=l2)
    thermostats: dict[str, FloorThermostat] = {
        KITCHEN: f_kitchen_written_while_living_room_is_polled,
        LIVING_ROOM: f_living_room_thermostat,
    }
    return _use_case(store, temperatures, thermostats, book, dispatcher, gate=load_gate)


@pytest.fixture
def open_gate() -> LoadGateStub:
    return LoadGateStub(held=frozenset(), gated=frozenset(), answers={})


@pytest.fixture
def f_shed_between_writes_cycle(
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_living_room_thermostat: FloorThermostatStub,
    open_gate: LoadGateStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    _floor_zone('kitchen', KITCHEN)
    _floor_zone('living-room', LIVING_ROOM)
    store.record_state(KITCHEN, _state('22.0'))
    store.record_state(LIVING_ROOM, _state('22.0'))
    thermostats: dict[str, FloorThermostat] = {
        KITCHEN: ConcurrentWriteThermostatStub(meanwhile=lambda: open_gate.hold('living-room')),
        LIVING_ROOM: f_living_room_thermostat,
    }
    return _use_case(store, temperatures, thermostats, book, dispatcher, gate=open_gate)


@pytest.mark.parametrize(
    (
        'f_poll_between_writes_cycle',
        'f_kitchen_written_while_living_room_is_polled',
        'outcome',
        'commands',
        'stamp',
        'events',
    ),
    [
        (
            (False, _state('22.0', heating=False), '10.0'),
            _state('28.0', heating=False),
            ZoneCycleOutcome.UNCHANGED,
            [],
            None,
            [_issued('kitchen', '28.0', MORNING)],
        ),
        (
            (False, _state('28.0'), '10.0'),
            _state('25.0', heating=False),
            ZoneCycleOutcome.APPLIED,
            [('setpoint', Decimal('28.0'))],
            MORNING,
            [_issued('kitchen', '28.0', MORNING), _issued('living-room', '28.0', MORNING)],
        ),
        (
            (False, _state('22.0', heating=False), '13.0'),
            _state('28.0', heating=False),
            ZoneCycleOutcome.UNCHANGED,
            [],
            None,
            [_issued('kitchen', '28.0', MORNING)],
        ),
        (
            (False, _state('22.0', heating=False), '13.0'),
            _state('22.0', heating=True),
            ZoneCycleOutcome.APPLIED,
            [('setpoint', Decimal('28.0'))],
            MORNING,
            [_issued('kitchen', '28.0', MORNING), _issued('living-room', '28.0', MORNING)],
        ),
    ],
    ids=[
        'poll_finds_the_target_already_set',
        'poll_finds_the_setpoint_moved_by_hand',
        'poll_finds_a_start_that_would_wait_at_its_target',
        'poll_finds_a_start_that_would_wait_heating',
    ],
    indirect=['f_poll_between_writes_cycle', 'f_kitchen_written_while_living_room_is_polled'],
)
def test_cycle_with_load_management_off_drives_each_zone_on_its_thermostat_as_polled_by_its_turn(
    f_poll_between_writes_cycle: RunHeatingCycleUseCase,
    f_kitchen_written_while_living_room_is_polled: ConcurrentWriteThermostatStub,
    f_living_room_thermostat: FloorThermostatStub,
    f_guard_book: GuardBook,
    f_events: list[DomainEvent],
    outcome: ZoneCycleOutcome,
    commands: list[tuple[str, object]],
    stamp: datetime | None,
    events: list[DomainEvent],
) -> None:
    output = f_poll_between_writes_cycle.execute(_at(MORNING))

    assert (
        output.outcomes,
        f_kitchen_written_while_living_room_is_polled.commands,
        f_living_room_thermostat.commands,
        {zone.slug: (zone.last_command_at, zone.last_dry_run_at) for zone in Zone.objects.all()},
        f_events,
        f_guard_book.last_permit_at(),
    ) == (
        {'kitchen': ZoneCycleOutcome.APPLIED, 'living-room': outcome},
        [('setpoint', Decimal('28.0'))],
        commands,
        {'kitchen': (MORNING, None), 'living-room': (stamp, None)},
        events,
        None,
    )


@pytest.mark.parametrize(
    ('f_poll_between_writes_cycle', 'f_kitchen_written_while_living_room_is_polled'),
    [((True, _state('28.0'), '10.0'), _state('25.0', heating=False))],
    indirect=True,
)
def test_live_start_a_poll_reveals_by_its_turn_waits_for_a_permit_it_did_not_ask_for(
    f_poll_between_writes_cycle: RunHeatingCycleUseCase,
    f_living_room_thermostat: FloorThermostatStub,
    f_guard_book: GuardBook,
    f_events: list[DomainEvent],
) -> None:
    output = f_poll_between_writes_cycle.execute(_at(MORNING))

    assert (
        output.outcomes,
        f_living_room_thermostat.commands,
        Zone.objects.get(slug='living-room').last_command_at,
        f_events,
        f_guard_book.last_permit_at(),
    ) == (
        {'kitchen': ZoneCycleOutcome.APPLIED, 'living-room': ZoneCycleOutcome.WAITING},
        [],
        None,
        [_issued('kitchen', '28.0', MORNING)],
        None,
    )


def test_zone_shed_while_the_cycle_writes_another_is_held_from_its_turn(
    f_shed_between_writes_cycle: RunHeatingCycleUseCase,
    f_living_room_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
) -> None:
    output = f_shed_between_writes_cycle.execute(_at(MORNING))

    assert (
        output.outcomes,
        f_living_room_thermostat.commands,
        Zone.objects.get(slug='living-room').last_command_at,
        f_events,
    ) == (
        {'kitchen': ZoneCycleOutcome.APPLIED, 'living-room': ZoneCycleOutcome.SHED},
        [],
        None,
        [_issued('kitchen', '28.0', MORNING)],
    )


@pytest.fixture
def write_gate(clock: MonotonicClockStub) -> KeyedWriteGate:
    return _write_gate(clock)


@pytest.fixture
def f_gated_house(
    request: pytest.FixtureRequest,
    clock: MonotonicClockStub,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
    write_gate: KeyedWriteGate,
    dispatcher: EventDispatcher,
) -> GatedHouse:
    one_gate: bool = request.param
    _floor_zone('kitchen', KITCHEN)
    store.record_state(KITCHEN, _state('22.0'))

    def cycle(zones: ZoneStore | None) -> RunHeatingCycleUseCase:
        writes = write_gate if one_gate else _write_gate(clock)
        return _use_case(store, temperatures, {KITCHEN: f_thermostat}, book, dispatcher, zones=zones, writes=writes)

    return _gated_house(clock, write_gate, cycle)


@pytest.fixture
def f_kitchen_shed_live(
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
    f_held_gate: LoadGateStub,
    dispatcher: EventDispatcher,
) -> GatedHouse:
    write_clock = MonotonicClockStub(now=0.0)
    writes = _write_gate(write_clock)
    _floor_zone('kitchen', KITCHEN, last_command_at=MORNING)
    store.record_state(KITCHEN, _state('22.0', power=False))
    writes.stored(writes.override('kitchen', MORNING))

    def cycle(zones: ZoneStore | None) -> RunHeatingCycleUseCase:
        return _use_case(
            store,
            temperatures,
            {KITCHEN: f_thermostat},
            book,
            dispatcher,
            zones=zones,
            gate=f_held_gate,
            writes=writes,
        )

    return _gated_house(write_clock, writes, cycle)


@pytest.fixture
def f_kitchen_restored_after_a_live_shed(f_kitchen_shed_live: GatedHouse, f_held_gate: LoadGateStub) -> GatedHouse:
    f_held_gate.release('kitchen')
    return f_kitchen_shed_live


@pytest.fixture
def living_room_shed_forty_minutes_ago(
    clock: MonotonicClockStub,
    store: ThermostatStateStore,
    f_living_room_thermostat: FloorThermostatStub,
    f_guard_book: GuardBook,
    write_gate: KeyedWriteGate,
) -> Callable[[], None]:
    ZoneLoadFactory.create(
        slug='living-room',
        zone=_floor_zone('living-room', LIVING_ROOM, last_command_at=MORNING - timedelta(minutes=40)),
        priority=7,
        phase=LoadPhase.L2,
        nominal_amps=Decimal('8.0'),
        live=True,
        shed_at=MORNING - timedelta(minutes=40),
        shed_reason=ShedReason.OVERLOAD,
        shed_phase=LoadPhase.L2,
        shed_device=LIVING_ROOM,
    )
    LoadSettingsFactory.create(load_management_live=True)
    store.record_state(LIVING_ROOM, _state('22.0', power=False))
    watch = WatchPhasesUseCase(
        loads=LoadRepository(),
        load_settings=LoadSettingsRepository(),
        devices=DeviceRepository(),
        meter=PhaseMeterStore(stale_after=timedelta(seconds=10), unavailable_after=UNAVAILABLE_AFTER, clock=clock),
        thermostats=store,
        plugs=PlugStateStore(unavailable_after=UNAVAILABLE_AFTER, clock=clock),
        climate_units=ClimateUnitStateStore(unavailable_after=UNAVAILABLE_AFTER, clock=clock),
        book=f_guard_book,
        clock=clock,
        dispatcher=EventDispatcher(),
    )
    shed = ShedPhaseLoadsUseCase(
        loads=LoadRepository(),
        switcher=LoadSwitcher(
            plugs=SwitchablePlugProviderStub(plugs={}),
            zones=ThermostatZoneSwitch(
                stamps=ZoneRepository(),
                thermostats=ThermostatProviderStub(thermostats={LIVING_ROOM: f_living_room_thermostat}),
                writes=write_gate,
            ),
            sources=SourceSwitchStub(writes=_write_gate(clock), error=None),
        ),
        book=f_guard_book,
        clock=clock,
        dry_run=False,
        dispatcher=EventDispatcher(),
    )

    def guard_second() -> None:
        clock.advance(1)
        now = _guarded_moment(clock)
        watch.execute(WatchPhasesInputDTO(now=now))
        shed.execute(ShedPhaseLoadsInputDTO(now=now))

    guard_second()
    return guard_second


@pytest.fixture
def f_living_room_released_while_the_cycle_writes_the_kitchen(
    clock: MonotonicClockStub,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_living_room_thermostat: FloorThermostatStub,
    f_guard_book: GuardBook,
    write_gate: KeyedWriteGate,
    living_room_shed_forty_minutes_ago: Callable[[], None],
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    _floor_zone('kitchen', KITCHEN)
    store.record_state(KITCHEN, _state('22.0'))

    def release_living_room() -> None:
        Zone.objects.filter(slug='living-room').update(live_control=False)
        clock.advance(59)
        living_room_shed_forty_minutes_ago()
        store.record_state(LIVING_ROOM, _state('22.0', heating=True, read_at=_guarded_moment(clock)))

    thermostats: dict[str, FloorThermostat] = {
        KITCHEN: ConcurrentWriteThermostatStub(meanwhile=release_living_room),
        LIVING_ROOM: f_living_room_thermostat,
    }
    return _use_case(
        store,
        temperatures,
        thermostats,
        book,
        dispatcher,
        gate=GuardLoadGate(book=f_guard_book, clock=clock),
        writes=write_gate,
    )


@pytest.fixture
def lock_contended() -> threading.Event:
    return threading.Event()


@pytest.fixture
def guard_thread() -> Iterator[ThreadPoolExecutor]:
    with ThreadPoolExecutor(max_workers=1) as executor:
        yield executor


@pytest.fixture
def uncontended_write_gate(clock: MonotonicClockStub) -> KeyedWriteGate:
    return KeyedWriteGate(clock=clock, key_lock=partial(ContendedLockStub, contended=_never_contended))


@pytest.fixture
def f_kitchen_shed_while_the_cycle_stamps_its_write(
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
    uncontended_write_gate: KeyedWriteGate,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    _floor_zone('kitchen', KITCHEN)
    store.record_state(KITCHEN, _state('22.0'))
    switch = ThermostatZoneSwitch(
        stamps=ZoneWriteStampsStub(error=None),
        thermostats=ThermostatProviderStub(thermostats={KITCHEN: f_thermostat}),
        writes=uncontended_write_gate,
    )
    zones = ConcurrentStampZoneStoreStub(
        zones=ZoneRepository.all_settings(),
        meanwhile=partial(switch.switch_off, 'kitchen', KITCHEN, MORNING + SECOND),
    )
    return _use_case(
        store,
        temperatures,
        {KITCHEN: f_thermostat},
        book,
        dispatcher,
        zones=zones,
        writes=uncontended_write_gate,
    )


@pytest.fixture
def f_living_room_shed_while_the_cycle_writes_the_kitchen(
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_living_room_thermostat: FloorThermostatStub,
    uncontended_write_gate: KeyedWriteGate,
    dispatcher: EventDispatcher,
) -> ShedRace:
    _floor_zone('kitchen', KITCHEN)
    _floor_zone('living-room', LIVING_ROOM)
    store.record_state(KITCHEN, _state('22.0'))
    store.record_state(LIVING_ROOM, _state('28.0'))
    switch = ThermostatZoneSwitch(
        stamps=ZoneWriteStampsStub(error=None),
        thermostats=ThermostatProviderStub(thermostats={LIVING_ROOM: f_living_room_thermostat}),
        writes=uncontended_write_gate,
    )
    kitchen = ConcurrentWriteThermostatStub(
        meanwhile=HookStub(partial(switch.switch_off, 'living-room', LIVING_ROOM, MORNING + SECOND)),
    )
    thermostats: dict[str, FloorThermostat] = {KITCHEN: kitchen, LIVING_ROOM: f_living_room_thermostat}
    return ShedRace(
        thermostat=kitchen,
        cycle=_use_case(store, temperatures, thermostats, book, dispatcher, writes=uncontended_write_gate),
        sheds=[],
    )


@pytest.fixture
def f_kitchen_shed_after_the_cycle_checked_its_slot(
    clock: MonotonicClockStub,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    lock_contended: threading.Event,
    guard_thread: ThreadPoolExecutor,
    dispatcher: EventDispatcher,
) -> ShedRace:
    _floor_zone('kitchen', KITCHEN)
    store.record_state(KITCHEN, _state('22.0', power=False))
    writes = KeyedWriteGate(clock=clock, key_lock=partial(ContendedLockStub, contended=lock_contended.set))
    sheds: list[Future[bool]] = []

    def shed_in_the_guard() -> None:
        sheds.append(guard_thread.submit(switch.switch_off, 'kitchen', KITCHEN, MORNING + SECOND))
        assert lock_contended.wait(timeout=WAIT)

    thermostat = PausingThermostatStub(meanwhile=HookStub(shed_in_the_guard))
    switch = ThermostatZoneSwitch(
        stamps=ZoneWriteStampsStub(error=None),
        thermostats=ThermostatProviderStub(thermostats={KITCHEN: thermostat}),
        writes=writes,
    )
    return ShedRace(
        thermostat=thermostat,
        cycle=_use_case(store, temperatures, {KITCHEN: thermostat}, book, dispatcher, writes=writes),
        sheds=sheds,
    )


@pytest.fixture
def f_kitchen_shed_in_flight_when_the_cycle_reaches_its_zone(
    clock: MonotonicClockStub,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    guard_thread: ThreadPoolExecutor,
    dispatcher: EventDispatcher,
) -> ShedRace:
    _floor_zone('kitchen', KITCHEN)
    store.record_state(KITCHEN, _state('22.0', power=False))
    shed_started = threading.Event()
    shed_may_finish = threading.Event()
    thermostat = InFlightPowerOffThermostatStub(started=shed_started, finish=shed_may_finish)
    writes = KeyedWriteGate(clock=clock, key_lock=partial(ContendedLockStub, contended=shed_may_finish.set))
    switch = ThermostatZoneSwitch(
        stamps=ZoneWriteStampsStub(error=None),
        thermostats=ThermostatProviderStub(thermostats={KITCHEN: thermostat}),
        writes=writes,
    )
    sheds: list[Future[bool]] = []

    def shed_in_the_guard() -> None:
        sheds.append(guard_thread.submit(switch.switch_off, 'kitchen', KITCHEN, MORNING + SECOND))
        assert shed_started.wait(timeout=WAIT)

    zones = InterleavedZoneStoreStub(
        before_read=HookStub(None),
        after_read=HookStub(None),
        before_stamp=HookStub(None),
        after_stamp=HookStub(shed_in_the_guard),
    )
    return ShedRace(
        thermostat=thermostat,
        cycle=_use_case(store, temperatures, {KITCHEN: thermostat}, book, dispatcher, zones=zones, writes=writes),
        sheds=sheds,
    )


@pytest.mark.parametrize(
    'f_gated_house',
    [True, False],
    ids=['one_gate_for_every_cycle', 'no_slot_kept_between_cycles'],
    indirect=True,
)
def test_write_gate_leaves_the_writes_stamps_outcomes_and_events_of_the_cycle_as_they_were(
    f_gated_house: GatedHouse,
    f_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
) -> None:
    first = f_gated_house.cycle().execute(_at(MORNING))
    throttled = f_gated_house.cycle().execute(_at(MORNING + timedelta(minutes=10)))
    second = f_gated_house.cycle().execute(_at(MORNING + HALF_HOUR))
    f_gated_house.boost.execute(BoostZoneInputDTO(zone='kitchen', now=MORNING + timedelta(minutes=35)))
    boosted = f_gated_house.cycle().execute(_at(MORNING + timedelta(minutes=36)))
    after_boost = f_gated_house.cycle().execute(_at(MORNING + timedelta(minutes=37)))

    assert (
        [output.outcomes['kitchen'] for output in (first, throttled, second, boosted, after_boost)],
        f_thermostat.commands,
        Zone.objects.get(slug='kitchen').last_command_at,
        f_events,
    ) == (
        [
            ZoneCycleOutcome.APPLIED,
            ZoneCycleOutcome.THROTTLED,
            ZoneCycleOutcome.APPLIED,
            ZoneCycleOutcome.APPLIED,
            ZoneCycleOutcome.THROTTLED,
        ],
        [('setpoint', Decimal('28.0')), ('setpoint', Decimal('28.0')), ('setpoint', Decimal('30.0'))],
        MORNING + timedelta(minutes=36),
        [
            _issued('kitchen', '28.0', MORNING),
            _issued('kitchen', '28.0', MORNING + HALF_HOUR),
            _issued('kitchen', '30.0', MORNING + timedelta(minutes=36)),
        ],
    )


@pytest.mark.parametrize(
    'f_gated_house',
    [True, False],
    ids=['one_gate_for_every_cycle', 'no_slot_kept_between_cycles'],
    indirect=True,
)
def test_write_gate_gives_a_refused_stamp_its_slot_back_so_the_next_cycle_writes_as_before(
    f_gated_house: GatedHouse,
    f_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
) -> None:
    refused = f_gated_house.locked_cycle().execute(_at(MORNING))
    retried = f_gated_house.cycle().execute(_at(MORNING + MINUTE))

    assert (
        [output.outcomes['kitchen'] for output in (refused, retried)],
        f_thermostat.commands,
        Zone.objects.get(slug='kitchen').last_command_at,
        f_events,
    ) == (
        [ZoneCycleOutcome.FAILED, ZoneCycleOutcome.APPLIED],
        [('setpoint', Decimal('28.0'))],
        MORNING + MINUTE,
        [_issued('kitchen', '28.0', MORNING + MINUTE)],
    )


def test_restored_zone_waits_thirty_minutes_after_its_live_shed_on_a_clock_stepped_forward(
    f_kitchen_restored_after_a_live_shed: GatedHouse,
    f_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
) -> None:
    f_kitchen_restored_after_a_live_shed.clock.advance(180)
    held = f_kitchen_restored_after_a_live_shed.cycle().execute(
        _at(STEPPED_FORWARD, monotonic=f_kitchen_restored_after_a_live_shed.clock()),
    )
    f_kitchen_restored_after_a_live_shed.clock.advance(1620)
    written = f_kitchen_restored_after_a_live_shed.cycle().execute(
        _at(STEPPED_FORWARD + timedelta(minutes=27), monotonic=f_kitchen_restored_after_a_live_shed.clock()),
    )

    assert (
        [output.outcomes['kitchen'] for output in (held, written)],
        f_thermostat.commands,
        Zone.objects.get(slug='kitchen').last_command_at,
        f_events,
    ) == (
        [ZoneCycleOutcome.THROTTLED, ZoneCycleOutcome.APPLIED],
        [('power', True), ('setpoint', Decimal('28.0'))],
        STEPPED_FORWARD + timedelta(minutes=27),
        [
            ZoneCommandIssued(
                zone='kitchen',
                change=ThermostatChange(power=True, setpoint=Decimal('28.0')),
                dry_run=False,
                occurred_at=STEPPED_FORWARD + timedelta(minutes=27),
            ),
        ],
    )


def test_boost_pressed_while_the_zone_is_shed_live_goes_out_thirty_minutes_after_the_shed(
    f_kitchen_shed_live: GatedHouse,
    f_held_gate: LoadGateStub,
    f_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
) -> None:
    f_kitchen_shed_live.boost.execute(BoostZoneInputDTO(zone='kitchen', now=MORNING + MINUTE))
    f_held_gate.release('kitchen')
    f_kitchen_shed_live.clock.advance(180)
    restored = f_kitchen_shed_live.cycle().execute(
        _at(MORNING + timedelta(minutes=3), monotonic=f_kitchen_shed_live.clock()),
    )
    f_kitchen_shed_live.clock.advance(1620)
    boosted = f_kitchen_shed_live.cycle().execute(_at(MORNING + HALF_HOUR, monotonic=f_kitchen_shed_live.clock()))

    assert (
        [output.outcomes['kitchen'] for output in (restored, boosted)],
        f_thermostat.commands,
        Zone.objects.get(slug='kitchen').last_command_at,
        f_held_gate.asked,
        f_events,
    ) == (
        [ZoneCycleOutcome.THROTTLED, ZoneCycleOutcome.APPLIED],
        [('power', True), ('setpoint', Decimal('30.0'))],
        MORNING + HALF_HOUR,
        [(), (ZoneStart(zone='kitchen', writes_live=True),)],
        [
            ZoneCommandIssued(
                zone='kitchen',
                change=ThermostatChange(power=True, setpoint=Decimal('30.0')),
                dry_run=False,
                occurred_at=MORNING + HALF_HOUR,
            ),
        ],
    )


def test_cycle_in_flight_with_an_older_stamp_writes_nothing_after_a_guard_release_of_its_zone(
    f_living_room_released_while_the_cycle_writes_the_kitchen: RunHeatingCycleUseCase,
    f_living_room_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
) -> None:
    output = f_living_room_released_while_the_cycle_writes_the_kitchen.execute(_at(MORNING))

    assert (
        output.outcomes,
        f_living_room_thermostat.commands,
        {zone.slug: zone.last_command_at for zone in Zone.objects.all()},
        f_events,
    ) == (
        {'kitchen': ZoneCycleOutcome.APPLIED, 'living-room': ZoneCycleOutcome.THROTTLED},
        [('power', True)],
        {'kitchen': MORNING, 'living-room': MORNING + timedelta(seconds=61)},
        [_issued('kitchen', '28.0', MORNING)],
    )


def test_shed_while_the_cycle_stamps_a_write_goes_out_at_once_and_the_write_goes_no_further(
    f_kitchen_shed_while_the_cycle_stamps_its_write: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
) -> None:
    output = f_kitchen_shed_while_the_cycle_stamps_its_write.execute(_at(MORNING))

    assert (output.outcomes, f_thermostat.commands, f_events) == (
        {'kitchen': ZoneCycleOutcome.THROTTLED},
        [('power', False)],
        [],
    )


def test_shed_of_another_zone_goes_out_at_once_while_the_cycle_writes_a_zone(
    f_living_room_shed_while_the_cycle_writes_the_kitchen: ShedRace,
    f_living_room_thermostat: FloorThermostatStub,
) -> None:
    output = f_living_room_shed_while_the_cycle_writes_the_kitchen.cycle.execute(_at(MORNING))

    assert (
        output.outcomes['kitchen'],
        f_living_room_shed_while_the_cycle_writes_the_kitchen.thermostat.commands,
        f_living_room_thermostat.commands[0],
    ) == (ZoneCycleOutcome.APPLIED, [('setpoint', Decimal('28.0'))], ('power', False))


def test_shed_that_takes_the_slot_after_the_cycle_checked_it_switches_off_once_the_cycles_write_is_done(
    f_kitchen_shed_after_the_cycle_checked_its_slot: ShedRace,
    f_events: list[DomainEvent],
) -> None:
    output = f_kitchen_shed_after_the_cycle_checked_its_slot.cycle.execute(_at(MORNING))
    stamped = f_kitchen_shed_after_the_cycle_checked_its_slot.sheds[0].result(timeout=WAIT)

    assert (
        output.outcomes,
        stamped,
        f_kitchen_shed_after_the_cycle_checked_its_slot.thermostat.commands,
        f_events,
    ) == (
        {'kitchen': ZoneCycleOutcome.APPLIED},
        True,
        [('power', True), ('setpoint', Decimal('28.0')), ('power', False)],
        [
            ZoneCommandIssued(
                zone='kitchen',
                change=ThermostatChange(power=True, setpoint=Decimal('28.0')),
                dry_run=False,
                occurred_at=MORNING,
            ),
        ],
    )


def test_cycle_write_that_reaches_its_zone_while_a_shed_is_in_flight_sends_nothing_after_the_shed(
    f_kitchen_shed_in_flight_when_the_cycle_reaches_its_zone: ShedRace,
    f_events: list[DomainEvent],
) -> None:
    output = f_kitchen_shed_in_flight_when_the_cycle_reaches_its_zone.cycle.execute(_at(MORNING))
    stamped = f_kitchen_shed_in_flight_when_the_cycle_reaches_its_zone.sheds[0].result(timeout=WAIT)

    assert (
        output.outcomes,
        stamped,
        f_kitchen_shed_in_flight_when_the_cycle_reaches_its_zone.thermostat.commands,
        f_events,
    ) == ({'kitchen': ZoneCycleOutcome.THROTTLED}, True, [('power', False)], [])


GATE_MODES = ((True, 'one_gate_for_every_cycle'), (False, 'no_slot_kept_between_cycles_as_on_master'))
RACE_POINTS = ('after_read', 'before_stamp', 'after_stamp', 'write')
BOOST_RACES = (
    ('boost_after_the_cycle_read_its_zones', {'after_read': 'boost'}, MORNING, ZoneCycleOutcome.THROTTLED),
    ('boost_between_the_reservation_and_the_stamp', {'before_stamp': 'boost'}, MORNING, ZoneCycleOutcome.THROTTLED),
    ('boost_between_the_stamp_and_the_write', {'after_stamp': 'boost'}, None, ZoneCycleOutcome.APPLIED),
    ('boost_during_the_write', {'write': 'boost'}, None, ZoneCycleOutcome.APPLIED),
    (
        'slot_freed_before_the_reservation_and_stamps_cleared_before_the_stamp',
        {'after_read': 'clear', 'before_stamp': 'store'},
        MORNING,
        ZoneCycleOutcome.THROTTLED,
    ),
    (
        'slot_freed_before_the_reservation_and_stamps_cleared_after_the_stamp',
        {'after_read': 'clear', 'after_stamp': 'store'},
        None,
        ZoneCycleOutcome.APPLIED,
    ),
    (
        'slot_freed_before_the_reservation_and_stamps_cleared_after_the_write',
        {'after_read': 'clear', 'write': 'store'},
        None,
        ZoneCycleOutcome.APPLIED,
    ),
)


@pytest.fixture
def f_boost_race(
    request: pytest.FixtureRequest,
    clock: MonotonicClockStub,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    write_gate: KeyedWriteGate,
    dispatcher: EventDispatcher,
) -> BoostRace:
    one_gate, steps = request.param
    _floor_zone('kitchen', KITCHEN)
    store.record_state(KITCHEN, _state('22.0'))
    deferred = DeferredBoostStoreStub()
    pressed = BoostZoneInputDTO(zone='kitchen', now=MORNING)
    actions: dict[str, Callable[[], object]] = {
        'boost': partial(
            BoostZoneUseCase(zones=ZoneRepository(), writes=write_gate, dispatcher=EventDispatcher()).execute,
            pressed,
        ),
        'clear': partial(
            BoostZoneUseCase(zones=deferred, writes=write_gate, dispatcher=EventDispatcher()).execute,
            pressed,
        ),
        'store': deferred.store,
    }
    hooks = {point: HookStub(actions[steps[point]] if point in steps else None) for point in RACE_POINTS}
    thermostat = ConcurrentWriteThermostatStub(meanwhile=hooks['write'])
    zones = InterleavedZoneStoreStub(
        before_read=HookStub(None),
        after_read=hooks['after_read'],
        before_stamp=hooks['before_stamp'],
        after_stamp=hooks['after_stamp'],
    )

    def cycle(cycle_zones: ZoneStore | None) -> RunHeatingCycleUseCase:
        writes = write_gate if one_gate else _write_gate(clock)
        return _use_case(store, temperatures, {KITCHEN: thermostat}, book, dispatcher, zones=cycle_zones, writes=writes)

    return BoostRace(thermostat=thermostat, racing_cycle=cycle(zones), next_cycle=cycle(None))


@pytest.fixture
def f_kitchen_switched_off_a_minute_after_its_write(f_gated_house: GatedHouse) -> GatedHouse:
    f_gated_house.cycle().execute(_at(MORNING))
    Zone.objects.filter(slug='kitchen').update(heating_enabled=False)
    f_gated_house.clock.advance(60)
    return f_gated_house


@pytest.fixture
def f_living_room_released_after_the_cycle_took_its_time(
    clock: MonotonicClockStub,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_living_room_thermostat: FloorThermostatStub,
    f_guard_book: GuardBook,
    write_gate: KeyedWriteGate,
    living_room_shed_forty_minutes_ago: Callable[[], None],
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    def release_living_room() -> None:
        Zone.objects.filter(slug='living-room').update(live_control=False)
        clock.advance(59)
        living_room_shed_forty_minutes_ago()
        Zone.objects.filter(slug='living-room').update(live_control=True)
        store.record_state(LIVING_ROOM, _state('22.0', heating=True, read_at=_guarded_moment(clock)))

    zones = InterleavedZoneStoreStub(
        before_read=HookStub(release_living_room),
        after_read=HookStub(None),
        before_stamp=HookStub(None),
        after_stamp=HookStub(None),
    )
    return _use_case(
        store,
        temperatures,
        {LIVING_ROOM: f_living_room_thermostat},
        book,
        dispatcher,
        zones=zones,
        gate=GuardLoadGate(book=f_guard_book, clock=clock),
        writes=write_gate,
    )


@pytest.mark.parametrize(
    ('f_boost_race', 'stamp', 'boost'),
    [
        pytest.param((one_gate, steps), stamp, boost, id=f'{race}-{mode}')
        for race, steps, stamp, boost in BOOST_RACES
        for one_gate, mode in GATE_MODES
    ],
    indirect=['f_boost_race'],
)
def test_boost_pressed_while_the_cycle_writes_its_zone_goes_out_when_the_stamp_rule_of_master_lets_it(
    f_boost_race: BoostRace,
    stamp: datetime | None,
    boost: ZoneCycleOutcome,
) -> None:
    raced = f_boost_race.racing_cycle.execute(_at(MORNING))
    stamped_at = Zone.objects.get(slug='kitchen').last_command_at
    boosted = f_boost_race.next_cycle.execute(_at(MORNING + MINUTE))

    assert (
        raced.outcomes['kitchen'],
        stamped_at,
        boosted.outcomes['kitchen'],
        boosted.outcomes['kitchen'] is ZoneCycleOutcome.APPLIED,
        f_boost_race.thermostat.commands[0],
    ) == (
        ZoneCycleOutcome.APPLIED,
        stamp,
        boost,
        write_allowed(stamp, MORNING + MINUTE),
        ('setpoint', Decimal('28.0')),
    )


@pytest.mark.parametrize(
    'f_gated_house',
    [one_gate for one_gate, _mode in GATE_MODES],
    ids=[mode for _one_gate, mode in GATE_MODES],
    indirect=True,
)
@pytest.mark.parametrize(
    ('now', 'expected', 'commands'),
    [
        (MORNING + MINUTE, ZoneCycleOutcome.THROTTLED, []),
        (MORNING + MINUTE - timedelta(seconds=70), ZoneCycleOutcome.APPLIED, [('power', False)]),
        (MORNING + MINUTE - timedelta(hours=1), ZoneCycleOutcome.APPLIED, [('power', False)]),
        (STEPPED_FORWARD, ZoneCycleOutcome.APPLIED, [('power', False)]),
    ],
    ids=[
        'clock_running_on',
        'clock_set_back_seventy_seconds',
        'clock_set_back_an_hour',
        'clock_stepped_forward_two_hours',
    ],
)
def test_write_after_a_clock_step_goes_out_when_the_stamp_rule_of_master_lets_it(
    f_kitchen_switched_off_a_minute_after_its_write: GatedHouse,
    f_thermostat: FloorThermostatStub,
    now: datetime,
    expected: ZoneCycleOutcome,
    commands: list[tuple[str, object]],
) -> None:
    output = f_kitchen_switched_off_a_minute_after_its_write.cycle().execute(
        _at(now, monotonic=f_kitchen_switched_off_a_minute_after_its_write.clock()),
    )

    assert (
        output.outcomes['kitchen'],
        output.outcomes['kitchen'] is ZoneCycleOutcome.APPLIED,
        f_thermostat.commands,
    ) == (expected, write_allowed(MORNING, now), [('setpoint', Decimal('28.0')), *commands])


def test_cycle_that_took_its_time_before_a_guard_release_it_then_read_writes_nothing_after_it(
    f_living_room_released_after_the_cycle_took_its_time: RunHeatingCycleUseCase,
    f_living_room_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
) -> None:
    output = f_living_room_released_after_the_cycle_took_its_time.execute(_at(MORNING))

    assert (
        output.outcomes,
        f_living_room_thermostat.commands,
        Zone.objects.get(slug='living-room').last_command_at,
        f_events,
    ) == (
        {'living-room': ZoneCycleOutcome.THROTTLED},
        [('power', True)],
        MORNING + timedelta(seconds=61),
        [],
    )


@pytest.fixture
def f_kitchen_handed_back_five_minutes_before_a_restart(
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> GatedHouse:
    write_clock = MonotonicClockStub(now=0.0)
    writes = _write_gate(write_clock)
    ZoneLoadFactory.create(
        slug='kitchen',
        zone=_floor_zone('kitchen', KITCHEN, last_command_at=MORNING - timedelta(minutes=5)),
        priority=5,
        phase=LoadPhase.L2,
        nominal_amps=Decimal('8.0'),
        live=True,
        shed_at=MORNING - timedelta(minutes=5),
        shed_reason=ShedReason.OVERLOAD,
        shed_phase=LoadPhase.L2,
        shed_device=KITCHEN,
        shed_handed_back=True,
    )
    store.record_state(KITCHEN, _state('22.0', power=False))
    RecoverWriteHoldsUseCase(
        loads=LoadRepository(),
        writes=writes,
        source_writes=_write_gate(MonotonicClockStub(now=0.0)),
        dry_run=False,
    ).execute(RecoverWriteHoldsInputDTO(now=MORNING))

    def cycle(zones: ZoneStore | None) -> RunHeatingCycleUseCase:
        return _use_case(store, temperatures, {KITCHEN: f_thermostat}, book, dispatcher, zones=zones, writes=writes)

    return _gated_house(write_clock, writes, cycle)


def test_boost_pressed_after_a_restart_keeps_the_write_budget_of_a_floor_handed_back_before_it(
    f_kitchen_handed_back_five_minutes_before_a_restart: GatedHouse,
    f_thermostat: FloorThermostatStub,
) -> None:
    f_kitchen_handed_back_five_minutes_before_a_restart.boost.execute(BoostZoneInputDTO(zone='kitchen', now=MORNING))
    f_kitchen_handed_back_five_minutes_before_a_restart.clock.advance(60)
    held = f_kitchen_handed_back_five_minutes_before_a_restart.cycle().execute(
        _at(MORNING + MINUTE, monotonic=f_kitchen_handed_back_five_minutes_before_a_restart.clock()),
    )
    stamped_at = Zone.objects.get(slug='kitchen').last_command_at
    f_kitchen_handed_back_five_minutes_before_a_restart.clock.advance(1440)
    boosted = f_kitchen_handed_back_five_minutes_before_a_restart.cycle().execute(
        _at(MORNING + timedelta(minutes=25), monotonic=f_kitchen_handed_back_five_minutes_before_a_restart.clock()),
    )

    assert (held.outcomes, stamped_at, boosted.outcomes, f_thermostat.commands) == (
        {'kitchen': ZoneCycleOutcome.THROTTLED},
        MORNING - timedelta(minutes=5),
        {'kitchen': ZoneCycleOutcome.APPLIED},
        [('power', True), ('setpoint', Decimal('30.0'))],
    )


def test_floor_handed_back_before_a_restart_waits_thirty_minutes_after_its_shed_on_a_clock_stepped_forward(
    f_kitchen_handed_back_five_minutes_before_a_restart: GatedHouse,
    f_thermostat: FloorThermostatStub,
) -> None:
    f_kitchen_handed_back_five_minutes_before_a_restart.clock.advance(60)
    held = f_kitchen_handed_back_five_minutes_before_a_restart.cycle().execute(
        _at(STEPPED_FORWARD, monotonic=f_kitchen_handed_back_five_minutes_before_a_restart.clock()),
    )
    f_kitchen_handed_back_five_minutes_before_a_restart.clock.advance(1440)
    written = f_kitchen_handed_back_five_minutes_before_a_restart.cycle().execute(
        _at(
            STEPPED_FORWARD + timedelta(minutes=24),
            monotonic=f_kitchen_handed_back_five_minutes_before_a_restart.clock(),
        ),
    )

    assert (held.outcomes, written.outcomes, f_thermostat.commands) == (
        {'kitchen': ZoneCycleOutcome.THROTTLED},
        {'kitchen': ZoneCycleOutcome.APPLIED},
        [('power', True), ('setpoint', Decimal('28.0'))],
    )


@pytest.fixture
def morning_house(
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    f_thermostat: FloorThermostatStub,
) -> dict[str, FloorThermostat]:
    Zone.objects.filter(pk=_air_zone().pk).update(name='Кухня')
    ClimateSettingsFactory.create(outdoor_sensor=_sensor('outdoor'))
    store.record_state(KITCHEN, _state('28.0'))
    temperatures.record_state('outdoor', _reading('-3.4'))
    return {KITCHEN: f_thermostat}


@pytest.fixture
def fireplace() -> HeatSource:
    return HeatSourceFactory.create(slug='fireplace', name='Камин', room=_open_space().room)


@pytest.fixture
def f_fast_morning_cycle(
    morning_house: dict[str, FloorThermostat],
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    _run_morning(store, temperatures, morning_house, book, FAST_MORNING_AIR)
    return _use_case(store, temperatures, morning_house, book, dispatcher)


@pytest.fixture
def f_marked_morning_cycle(
    morning_house: dict[str, FloorThermostat],
    fireplace: HeatSource,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    Zone.objects.filter(slug='kitchen').update(room=fireplace.room)
    _run_morning(store, temperatures, morning_house, book, MORNING_AIR[:3])
    HeatSource.objects.filter(pk=fireplace.pk).update(lit_until=MORNING_AIR[3][0] + timedelta(hours=1))
    _run_morning(store, temperatures, morning_house, book, MORNING_AIR[3:])
    return _use_case(store, temperatures, morning_house, book, dispatcher)


@pytest.fixture
def f_marked_stop_cycle(
    stop_thermostats: dict[str, FloorThermostat],
    fireplace: HeatSource,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    Zone.objects.filter(slug='kitchen').update(room=fireplace.room)
    _run_stop(store, temperatures, stop_thermostats, book, STOP_AIR[:2])
    HeatSource.objects.filter(pk=fireplace.pk).update(lit_until=STOP_AIR[2][0] + timedelta(hours=1))
    _run_stop(store, temperatures, stop_thermostats, book, STOP_AIR[2:])
    return _use_case(store, temperatures, stop_thermostats, book, dispatcher)


@pytest.fixture
def f_fire_cycle(
    request: pytest.FixtureRequest,
    stop_thermostats: dict[str, FloorThermostat],
    fireplace: HeatSource,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    Zone.objects.filter(slug='kitchen').update(room=fireplace.room)
    HeatSource.objects.filter(pk=fireplace.pk).update(live=request.param)
    book.remember_zone(
        'kitchen',
        ZoneMemory(
            path=ControlPath.AIR,
            mode=ScheduleMode.COMFORT,
            air_state=AirState.IDLE,
            heat_target=Decimal('22.0'),
            stop=StopWatch(
                stopped_at=MORNING - timedelta(minutes=10),
                target=Decimal('22.0'),
                offset=Decimal('0.10'),
                peak=Decimal('22.1'),
            ),
        ),
    )
    store.record_state(KITCHEN, _state('18.0'))
    for minute in range(FIRE_MINUTES - 1):
        temperatures.record_state('air-kitchen', _reading(_fire_air(minute)))
        _use_case(store, temperatures, stop_thermostats, book, EventDispatcher()).execute(
            _at(MORNING + minute * MINUTE, monotonic=minute * 60.0),
        )
    temperatures.record_state('air-kitchen', _reading(_fire_air(FIRE_MINUTES - 1)))
    return _use_case(store, temperatures, stop_thermostats, book, dispatcher)


@pytest.fixture
def f_reset_meanwhile_cycle(
    morning_thermostats: dict[str, FloorThermostat],
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    _learned(Zone.objects.get(slug='kitchen'))
    learning = ResetBeforeSaveLearningStub()
    return _use_case(store, temperatures, morning_thermostats, book, dispatcher, learning=learning)


@pytest.fixture
def f_fenced_morning_cycle(
    morning_house: dict[str, FloorThermostat],
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
) -> RunHeatingCycleUseCase:
    _learned(Zone.objects.get(slug='kitchen'))
    _run_morning(store, temperatures, morning_house, book, MORNING_AIR[:3])
    ZoneLearningRepository.reset('kitchen')
    temperatures.record_state('air-kitchen', _reading(MORNING_AIR[3][1]))
    return _use_case(store, temperatures, morning_house, book, EventDispatcher())


def test_morning_faster_than_its_cap_teaches_nothing_and_is_journaled(
    f_fast_morning_cycle: RunHeatingCycleUseCase,
    f_events: list[DomainEvent],
) -> None:
    f_fast_morning_cycle.execute(_at(COMFORT_AT))

    assert (ZoneLearning.objects.exists(), [event for event in f_events if isinstance(event, ZoneMorningRejected)]) == (
        False,
        [
            ZoneMorningRejected(
                zone='kitchen',
                name='Кухня',
                rate=Decimal('2.280'),
                cap=Decimal('1.5'),
                outdoor_bucket=-5,
                dead_time_minutes=10,
                occurred_at=COMFORT_AT,
            ),
        ],
    )


def test_fireplace_marked_during_a_morning_drops_it(
    f_marked_morning_cycle: RunHeatingCycleUseCase,
    book: ControlBook,
) -> None:
    f_marked_morning_cycle.execute(_at(COMFORT_AT))

    assert (ZoneLearning.objects.exists(), book.zone('kitchen').morning) == (False, None)


def test_fireplace_marked_during_an_early_stop_watch_drops_it(
    f_marked_stop_cycle: RunHeatingCycleUseCase,
    book: ControlBook,
) -> None:
    f_marked_stop_cycle.execute(_at(MORNING + timedelta(hours=2, minutes=1)))

    assert (ZoneLearning.objects.exists(), book.zone('kitchen').stop) == (False, None)


@pytest.mark.parametrize(
    ('f_fire_cycle', 'watching'),
    [(False, True), (True, False)],
    ids=['watching', 'live'],
    indirect=['f_fire_cycle'],
)
def test_detected_fire_is_journaled_and_withholds_learning_only_once_the_fireplace_is_live(
    f_fire_cycle: RunHeatingCycleUseCase,
    f_events: list[DomainEvent],
    book: ControlBook,
    watching: bool,
) -> None:
    now = MORNING + (FIRE_MINUTES - 1) * MINUTE

    f_fire_cycle.execute(_at(now, monotonic=(FIRE_MINUTES - 1) * 60.0))

    assert (
        [event for event in f_events if isinstance(event, ExternalHeatStarted | ExternalHeatEnded)],
        book.burning_rooms(),
        book.zone('kitchen').stop is not None,
    ) == (
        [
            ExternalHeatStarted(
                room='open-space',
                room_name='Гостиная, кухня и прихожая',
                rates={'air-kitchen': Decimal('1.80')},
                live=not watching,
                occurred_at=now,
            ),
        ],
        frozenset({'open-space'}),
        watching,
    )


def test_morning_whose_learning_was_reset_meanwhile_is_not_saved_back(
    f_reset_meanwhile_cycle: RunHeatingCycleUseCase,
) -> None:
    with capture_logs() as logs:
        f_reset_meanwhile_cycle.execute(_at(COMFORT_AT))

    learning = ZoneLearning.objects.get(zone__slug='kitchen')
    assert (
        (learning.dead_time, learning.heat_up_rates, learning.mornings, learning.resets),
        [log['event'] for log in logs if log['event'] in ('zone_learning_reset_meanwhile', 'zone_morning_learned')],
    ) == ((None, {}, 0, 1), ['zone_learning_reset_meanwhile'])


def test_reset_drops_the_morning_in_progress_at_the_next_cycle(
    f_fenced_morning_cycle: RunHeatingCycleUseCase,
    book: ControlBook,
) -> None:
    morning_before = book.zone('kitchen').morning

    f_fenced_morning_cycle.execute(_at(MORNING_AIR[3][0]))

    memory = book.zone('kitchen')
    assert (morning_before is not None, memory.morning, memory.resets) == (True, None, 1)


@pytest.fixture
def f_permits() -> LoadGateStub:
    return LoadGateStub(
        held=frozenset(),
        gated=frozenset({'kitchen'}),
        answers={'kitchen': StartAnswer(granted=True, note=None)},
    )


@pytest.fixture
def f_held_by_hand_cycle(
    request: pytest.FixtureRequest,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
    f_permits: LoadGateStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    _floor_zone('kitchen', KITCHEN, manual_until=HELD_UNTIL)
    ClimateSettingsFactory.create(manual_hold_live=request.param)
    store.record_state(KITCHEN, _state('22.0', heating=False))
    return _use_case(store, temperatures, {KITCHEN: f_thermostat}, book, dispatcher, gate=f_permits)


@pytest.fixture
def f_shed_held_by_hand_cycle(
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    _floor_zone('kitchen', KITCHEN, manual_until=HELD_UNTIL, last_command_at=MORNING - HALF_HOUR)
    ClimateSettingsFactory.create(manual_hold_live=True)
    store.record_state(KITCHEN, _state('22.0', power=False))
    gate = LoadGateStub(held=frozenset({'kitchen'}), gated=frozenset({'kitchen'}), answers={})
    return _use_case(store, temperatures, {KITCHEN: f_thermostat}, book, dispatcher, gate=gate)


@pytest.fixture
def f_held_backup_cycle(
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_backup_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    _house_settings(backup_live=True)
    ClimateSettings.objects.update(manual_hold_live=True)
    Zone.objects.filter(slug='bathroom-2f').update(manual_until=NIGHT + timedelta(hours=1))
    store.record_state(BATHROOM, _state('22.0'))
    temperatures.record_state('air-second-floor', _reading('18.5'))
    return _use_case(store, temperatures, {BATHROOM: f_backup_thermostat}, book, dispatcher)


@pytest.fixture
def f_held_overridden_cycle(
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_living_room_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    climate = _overridden_open_space('22.5', MORNING + timedelta(hours=1))
    _open_space_zone(climate, kind=ZoneKind.FLOOR, manual_until=HELD_UNTIL)
    ClimateSettingsFactory.create(manual_hold_live=True)
    store.record_state(LIVING_ROOM, _state('28.0'))
    return _use_case(store, temperatures, {LIVING_ROOM: f_living_room_thermostat}, book, dispatcher)


@pytest.mark.parametrize(
    ('f_held_by_hand_cycle', 'outcome', 'commands', 'asked', 'events'),
    [
        (True, ZoneCycleOutcome.HANDS_OFF, [], [()], []),
        (
            False,
            ZoneCycleOutcome.APPLIED,
            [('setpoint', Decimal('28.0'))],
            [(ZoneStart(zone='kitchen', writes_live=True),)],
            [_issued('kitchen', '28.0', MORNING)],
        ),
    ],
    ids=['gate_on', 'gate_off_reverts_the_change_at_the_next_allowed_write'],
    indirect=['f_held_by_hand_cycle'],
)
def test_floor_changed_on_its_thermostat_takes_no_write_and_asks_no_permit_while_its_hold_counts(
    f_held_by_hand_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_permits: LoadGateStub,
    f_events: list[DomainEvent],
    outcome: ZoneCycleOutcome,
    commands: list[tuple[str, Decimal]],
    asked: list[tuple[ZoneStart, ...]],
    events: list[DomainEvent],
) -> None:
    output = f_held_by_hand_cycle.execute(_at(MORNING))

    assert (output.outcomes, f_thermostat.commands, f_permits.asked, f_events) == (
        {'kitchen': outcome},
        commands,
        asked,
        events,
    )


def test_floor_shed_under_a_hold_by_hand_stays_shed(
    f_shed_held_by_hand_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    output = f_shed_held_by_hand_cycle.execute(_at(MORNING))

    assert (output.outcomes, f_thermostat.commands) == ({'kitchen': ZoneCycleOutcome.SHED}, [])


def test_hold_by_hand_wins_over_backup_heat(
    f_held_backup_cycle: RunHeatingCycleUseCase,
    f_backup_thermostat: FloorThermostatStub,
) -> None:
    output = f_held_backup_cycle.execute(_at(NIGHT))

    assert (output.outcomes, f_backup_thermostat.commands) == ({'bathroom-2f': ZoneCycleOutcome.HANDS_OFF}, [])


def test_hold_by_hand_wins_over_a_room_target_the_owner_forced(
    f_held_overridden_cycle: RunHeatingCycleUseCase,
    f_living_room_thermostat: FloorThermostatStub,
) -> None:
    output = f_held_overridden_cycle.execute(_at(MORNING))

    assert (output.outcomes, f_living_room_thermostat.commands) == ({'living-room': ZoneCycleOutcome.HANDS_OFF}, [])
