from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from django.db import OperationalError

from apps.core.domain.events import DomainEvent, EventDispatcher
from apps.core.stubs import MonotonicClockStub
from apps.devices.domain.kinds import DeviceKind
from apps.devices.domain.readings import Phase, PlugState
from apps.devices.services.phase_meter_store import PhaseMeterStore
from apps.devices.services.state_store import PlugStateStore, ThermostatStateStore
from apps.power.domain.control import LoadControl, PhaseThresholds
from apps.power.domain.events import (
    EvChargingStarted,
    EvChargingStopped,
    LoadPhaseLearned,
    PhaseCalmed,
    PhaseOverloaded,
)
from apps.power.domain.loads import LearnedPhase, LoadKind, LoadPhase, LoadRecord
from apps.power.services.guard_book import GuardBook
from apps.power.stubs import DeviceDirectoryStub, LoadSettingsStoreStub, LoadStoreStub
from apps.power.usecases.watch_phases import WatchPhasesInputDTO, WatchPhasesOutputDTO, WatchPhasesUseCase

NOW = datetime(2026, 11, 2, 18, 40, tzinfo=UTC)
START = 1000.0
METER = 'phase-meter'
CONTROL = LoadControl(
    live=False,
    thresholds=PhaseThresholds(
        start_ceiling=Decimal('20.0'),
        shed=Decimal('24.0'),
        shed_hold=timedelta(seconds=3),
        restore=Decimal('18.0'),
        restore_hold=timedelta(minutes=2),
        shed_interval=timedelta(seconds=1),
        restore_interval=timedelta(minutes=1),
        start_interval=timedelta(seconds=30),
        meter_lost_after=timedelta(seconds=60),
    ),
    default_amps=Decimal('6.0'),
)
BOILER = LoadRecord(
    slug='boiler-1f',
    name='Бойлер 1 этаж',
    kind=LoadKind.PLUG,
    zone_slug=None,
    device_slug='plug-boiler-1f',
    priority=1,
    phase=LoadPhase.UNKNOWN,
    nominal_amps=Decimal('6.0'),
    learned=None,
    live=False,
    shed=None,
)
BOILER_SWITCHED_ON = (range(25, 55), range(85, 141))
BOILER_POLLS = {10: False, 40: True, 70: False, 100: True}
EV_CHARGING = range(20, 320)
QUIET = {LoadPhase.L1: '8.0', LoadPhase.L2: '5.0', LoadPhase.L3: '4.0'}


@dataclass(frozen=True, kw_only=True)
class GuardRig:
    clock: MonotonicClockStub
    meter: PhaseMeterStore
    plugs: PlugStateStore
    loads: LoadStoreStub
    events: list[DomainEvent]
    use_case: WatchPhasesUseCase


def _at(second: int) -> datetime:
    return NOW + timedelta(seconds=second)


def _second(rig: GuardRig, amps: dict[LoadPhase, str], boiler_on: bool | None = None) -> WatchPhasesOutputDTO:
    rig.clock.advance(1)
    second = int(rig.clock() - START)
    if boiler_on is not None:
        rig.plugs.record_state('plug-boiler-1f', _boiler(boiler_on, second))
    for phase, load_phase in zip(Phase, (LoadPhase.L1, LoadPhase.L2, LoadPhase.L3), strict=True):
        rig.meter.record_current(METER, phase, Decimal(amps.get(load_phase, QUIET[load_phase])), _at(second))
    return rig.use_case.execute(WatchPhasesInputDTO(now=_at(second)))


def _seconds(rig: GuardRig, count: int, amps: dict[LoadPhase, str]) -> list[WatchPhasesOutputDTO]:
    return [_second(rig, amps) for _ in range(count)]


def _switch_boiler_three_times(rig: GuardRig) -> list[WatchPhasesOutputDTO]:
    outputs: list[WatchPhasesOutputDTO] = []
    for second in range(1, 141):
        switched_on = any(second in switched for switched in BOILER_SWITCHED_ON)
        outputs.append(_second(rig, {LoadPhase.L2: '11.0' if switched_on else '5.0'}, BOILER_POLLS.get(second)))
    return outputs


def _boiler(on: bool, second: int) -> PlugState:
    return PlugState(
        on=on,
        power_watts=Decimal('1305.4') if on else Decimal(0),
        current_amps=Decimal('6.02') if on else Decimal(0),
        voltage_volts=Decimal('216.8'),
        read_at=_at(second),
    )


def _rig(write_errors: tuple[Exception, ...]) -> GuardRig:
    clock = MonotonicClockStub(now=START)
    events: list[DomainEvent] = []
    dispatcher = EventDispatcher()
    for event_type in (PhaseOverloaded, PhaseCalmed, LoadPhaseLearned, EvChargingStarted, EvChargingStopped):
        dispatcher.register(event_type, events.append)
    meter = PhaseMeterStore(stale_after=timedelta(seconds=10), unavailable_after=timedelta(minutes=10), clock=clock)
    plugs = PlugStateStore(unavailable_after=timedelta(minutes=10), clock=clock)
    loads = LoadStoreStub(records=[BOILER], live_zones=frozenset(), write_errors=write_errors)
    return GuardRig(
        clock=clock,
        meter=meter,
        plugs=plugs,
        loads=loads,
        events=events,
        use_case=WatchPhasesUseCase(
            loads=loads,
            load_settings=LoadSettingsStoreStub(control=CONTROL),
            devices=DeviceDirectoryStub(slugs={DeviceKind.PHASE_METER: [METER]}),
            meter=meter,
            thermostats=ThermostatStateStore(unavailable_after=timedelta(minutes=10), clock=clock),
            plugs=plugs,
            book=GuardBook(),
            clock=clock,
            dispatcher=dispatcher,
        ),
    )


@pytest.fixture
def f_rig() -> GuardRig:
    return _rig(())


@pytest.fixture
def f_rig_whose_database_fails() -> GuardRig:
    return _rig((OperationalError('database is locked'),))


@pytest.fixture
def f_overloaded_rig(f_rig: GuardRig) -> GuardRig:
    _seconds(f_rig, 4, {LoadPhase.L2: '26.0'})
    return f_rig


def test_phase_held_three_seconds_above_the_shed_threshold_is_journaled_overloaded(f_rig: GuardRig) -> None:
    outputs = _seconds(f_rig, 4, {LoadPhase.L2: '26.0'})

    assert ([output.overloaded for output in outputs], f_rig.events) == (
        [(), (), (), (LoadPhase.L2,)],
        [PhaseOverloaded(phase=LoadPhase.L2, amps=Decimal('26.0'), occurred_at=_at(4))],
    )


def test_overload_ends_after_two_minutes_at_or_below_the_restore_threshold(f_overloaded_rig: GuardRig) -> None:
    outputs = _seconds(f_overloaded_rig, 121, {LoadPhase.L2: '17.0'})

    assert ([second for second, output in enumerate(outputs, start=5) if output.calmed], f_overloaded_rig.events) == (
        [125],
        [
            PhaseOverloaded(phase=LoadPhase.L2, amps=Decimal('26.0'), occurred_at=_at(4)),
            PhaseCalmed(phase=LoadPhase.L2, amps=Decimal('17.0'), occurred_at=_at(125)),
        ],
    )


def test_loads_are_read_from_the_store_once_a_minute(f_rig: GuardRig) -> None:
    _seconds(f_rig, 60, QUIET)
    reads_within_the_minute = f_rig.loads.reads
    _second(f_rig, QUIET)

    assert (reads_within_the_minute, f_rig.loads.reads) == (1, 2)


def test_plug_switching_three_times_on_one_phase_teaches_its_phase(f_rig: GuardRig) -> None:
    outputs = _switch_boiler_three_times(f_rig)

    learned = LearnedPhase(phase=LoadPhase.L2, amps=Decimal('6.0'), samples=3)
    assert (
        [second for second, output in enumerate(outputs, start=1) if output.learned],
        f_rig.loads.writes,
        f_rig.events,
    ) == (
        [135],
        [('save_learned', 'boiler-1f', learned)],
        [
            LoadPhaseLearned(
                load='boiler-1f',
                name='Бойлер 1 этаж',
                phase=LoadPhase.L2,
                amps=Decimal('6.0'),
                samples=3,
                occurred_at=_at(135),
            ),
        ],
    )


def test_learned_phase_the_database_fails_to_store_is_left_for_the_next_vote(
    f_rig_whose_database_fails: GuardRig,
) -> None:
    outputs = _switch_boiler_three_times(f_rig_whose_database_fails)

    assert (
        [second for second, output in enumerate(outputs, start=1) if output.learned],
        f_rig_whose_database_fails.loads.writes,
        f_rig_whose_database_fails.events,
    ) == ([], [], [])


def test_rise_no_load_explains_held_five_minutes_is_an_ev_charging_until_it_drops_back(f_rig: GuardRig) -> None:
    for second in range(1, 331):
        _second(f_rig, {LoadPhase.L3: '20.0' if second in EV_CHARGING else '4.0'})

    assert f_rig.events == [
        EvChargingStarted(phase=LoadPhase.L3, amps=Decimal('16.0'), before=Decimal('4.0'), occurred_at=_at(306)),
        EvChargingStopped(phase=LoadPhase.L3, amps=Decimal('16.0'), before=Decimal('4.0'), occurred_at=_at(322)),
    ]
