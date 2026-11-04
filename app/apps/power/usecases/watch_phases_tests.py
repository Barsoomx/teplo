from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from django.db import OperationalError

from apps.core.domain.events import DomainEvent, EventDispatcher
from apps.core.stubs import MonotonicClockStub
from apps.devices.domain.kinds import DeviceKind
from apps.devices.domain.readings import (
    ClimateMode,
    ClimateUnitState,
    Phase,
    PlugState,
    ThermostatMode,
    ThermostatState,
)
from apps.devices.services.phase_meter_store import PhaseMeterStore
from apps.devices.services.state_store import ClimateUnitStateStore, PlugStateStore, ThermostatStateStore
from apps.power.domain.control import EstimateSettings, LoadControl, PhaseThresholds
from apps.power.domain.estimate import EstimateBasis, EstimatePart, PhaseEstimate
from apps.power.domain.events import (
    EvChargingStarted,
    EvChargingStopped,
    LoadPhaseLearned,
    PhaseCalmed,
    PhaseOverloaded,
)
from apps.power.domain.guard import PhaseState
from apps.power.domain.load_status import LoadStatus
from apps.power.domain.loads import LearnedPhase, LoadKind, LoadPhase, LoadRecord
from apps.power.domain.meter_watch import MeterCondition
from apps.power.domain.view import GuardView
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
BOILER_2F = replace(
    BOILER,
    slug='boiler-2f',
    name='Бойлер 2 этаж',
    device_slug='plug-boiler-2f',
    priority=2,
    phase=LoadPhase.L2,
)
BATHROOM = LoadRecord(
    slug='bathroom-1f',
    name='Санузел 1 этаж',
    kind=LoadKind.ZONE,
    zone_slug='bathroom-1f',
    device_slug='thermostat-bathroom-1f',
    priority=3,
    phase=LoadPhase.L2,
    nominal_amps=Decimal('16.0'),
    learned=None,
    live=False,
    shed=None,
)
CONVECTOR = replace(
    BATHROOM,
    slug='convector-office',
    name='Конвектор: кабинет',
    kind=LoadKind.SOURCE,
    zone_slug=None,
    device_slug='heater-office',
    priority=8,
    nominal_amps=None,
    source_slug='convector-office',
    write_interval=timedelta(minutes=5),
    rated_amps=Decimal('8.7'),
)
ESTIMATING_L2 = replace(CONTROL, estimate=EstimateSettings(phase=LoadPhase.L2, base_amps=Decimal('2.0')))
ESTIMATE_BASE = EstimatePart(load=None, amps=Decimal('2.0'), basis=EstimateBasis.BASE)
BATHROOM_START = LoadStatus(
    slug='bathroom-1f',
    kind=LoadKind.ZONE,
    priority=3,
    phase=LoadPhase.L2,
    amps=Decimal('6.0'),
    available=True,
    drawing=False,
    shed=False,
)
BOILER_SWITCHED_ON = (range(25, 55), range(85, 141))
BOILER_POLLS = {10: False, 40: True, 70: False, 100: True}
EV_CHARGING = range(20, 320)
QUIET = {LoadPhase.L1: '8.0', LoadPhase.L2: '5.0', LoadPhase.L3: '4.0'}
WITHOUT_AN_L2_CLAMP = {LoadPhase.L2: '0.2'}


@dataclass(frozen=True, kw_only=True)
class GuardRig:
    clock: MonotonicClockStub
    meter: PhaseMeterStore
    plugs: PlugStateStore
    thermostats: ThermostatStateStore
    climate_units: ClimateUnitStateStore
    loads: LoadStoreStub
    book: GuardBook
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


def _seconds_without(rig: GuardRig, count: int, silent: LoadPhase) -> None:
    for _ in range(count):
        rig.clock.advance(1)
        second = int(rig.clock() - START)
        for phase, load_phase in zip(Phase, (LoadPhase.L1, LoadPhase.L2, LoadPhase.L3), strict=True):
            if load_phase is not silent:
                rig.meter.record_current(METER, phase, Decimal(QUIET[load_phase]), _at(second))
        rig.use_case.execute(WatchPhasesInputDTO(now=_at(second)))


def _switch_boiler_three_times(rig: GuardRig) -> list[WatchPhasesOutputDTO]:
    outputs: list[WatchPhasesOutputDTO] = []
    for second in range(1, 141):
        switched_on = any(second in switched for switched in BOILER_SWITCHED_ON)
        outputs.append(_second(rig, {LoadPhase.L2: '11.0' if switched_on else '5.0'}, BOILER_POLLS.get(second)))
    return outputs


def _phases(rig: GuardRig) -> PhaseState:
    state = rig.book.state()
    assert state is not None
    return state.phases


def _bathroom(*, heating: bool, second: int) -> ThermostatState:
    return ThermostatState(
        power=True,
        mode=ThermostatMode.HOLD,
        setpoint=Decimal('18.0'),
        floor_temperature=Decimal('16.5') if heating else Decimal('18.5'),
        heating=heating,
        read_at=_at(second),
    )


def _convector(*, on: bool, second: int) -> ClimateUnitState:
    return ClimateUnitState(
        mode=ClimateMode.HEAT if on else ClimateMode.OFF,
        target=Decimal(22),
        room_temperature=Decimal(23),
        read_at=_at(second),
        level='High',
    )


def _boiler(on: bool, second: int) -> PlugState:
    return PlugState(
        on=on,
        power_watts=Decimal('1305.4') if on else Decimal(0),
        current_amps=Decimal('6.02') if on else Decimal(0),
        voltage_volts=Decimal('216.8'),
        read_at=_at(second),
    )


def _rig(write_errors: tuple[Exception, ...], records: list[LoadRecord], control: LoadControl) -> GuardRig:
    clock = MonotonicClockStub(now=START)
    events: list[DomainEvent] = []
    dispatcher = EventDispatcher()
    for event_type in (PhaseOverloaded, PhaseCalmed, LoadPhaseLearned, EvChargingStarted, EvChargingStopped):
        dispatcher.register(event_type, events.append)
    meter = PhaseMeterStore(stale_after=timedelta(seconds=10), unavailable_after=timedelta(minutes=10), clock=clock)
    plugs = PlugStateStore(unavailable_after=timedelta(minutes=10), clock=clock)
    thermostats = ThermostatStateStore(unavailable_after=timedelta(minutes=10), clock=clock)
    climate_units = ClimateUnitStateStore(unavailable_after=timedelta(minutes=10), clock=clock)
    loads = LoadStoreStub(records=records, live_zones=frozenset(), write_errors=write_errors)
    book = GuardBook()
    return GuardRig(
        clock=clock,
        meter=meter,
        plugs=plugs,
        thermostats=thermostats,
        climate_units=climate_units,
        loads=loads,
        book=book,
        events=events,
        use_case=WatchPhasesUseCase(
            loads=loads,
            load_settings=LoadSettingsStoreStub(control=control),
            devices=DeviceDirectoryStub(slugs={DeviceKind.PHASE_METER: [METER]}),
            meter=meter,
            thermostats=thermostats,
            plugs=plugs,
            climate_units=climate_units,
            book=book,
            clock=clock,
            dispatcher=dispatcher,
        ),
    )


def _rig_on_l2(control: LoadControl) -> GuardRig:
    rig = _rig((), [BOILER, BOILER_2F, BATHROOM], control)
    rig.plugs.record_state('plug-boiler-2f', _boiler(True, 0))
    rig.thermostats.record_state('thermostat-bathroom-1f', _bathroom(heating=True, second=0))
    return rig


@pytest.fixture
def f_rig() -> GuardRig:
    return _rig((), [BOILER], CONTROL)


@pytest.fixture
def f_rig_whose_database_fails() -> GuardRig:
    return _rig((OperationalError('database is locked'),), [BOILER], CONTROL)


@pytest.fixture
def f_rig_estimating_l2() -> GuardRig:
    return _rig_on_l2(ESTIMATING_L2)


@pytest.fixture
def f_rig_measuring_l2() -> GuardRig:
    return _rig_on_l2(CONTROL)


@pytest.fixture
def f_rig_with_a_convector(request: pytest.FixtureRequest) -> GuardRig:
    rig = _rig((), [BOILER, CONVECTOR], ESTIMATING_L2)
    rig.climate_units.record_state('heater-office', _convector(on=request.param, second=0))
    return rig


@pytest.fixture
def f_rig_estimating_an_empty_l2() -> GuardRig:
    return _rig((), [BOILER], ESTIMATING_L2)


@pytest.fixture
def f_rig_whose_l2_went_silent(f_rig_estimating_l2: GuardRig) -> GuardRig:
    _seconds(f_rig_estimating_l2, 2, WITHOUT_AN_L2_CLAMP)
    _seconds_without(f_rig_estimating_l2, 15, LoadPhase.L2)
    return f_rig_estimating_l2


@pytest.fixture
def f_rig_whose_bathroom_floor_was_granted_a_start() -> GuardRig:
    rig = _rig((), [BOILER, BOILER_2F, replace(BATHROOM, nominal_amps=None)], ESTIMATING_L2)
    rig.plugs.record_state('plug-boiler-2f', _boiler(False, 0))
    rig.thermostats.record_state('thermostat-bathroom-1f', _bathroom(heating=False, second=0))
    _seconds(rig, 1, WITHOUT_AN_L2_CLAMP)
    rig.book.permit_start(
        BATHROOM_START,
        meter=MeterCondition.FRESH,
        view=GuardView(
            currents={LoadPhase.L1: Decimal('8.0'), LoadPhase.L2: Decimal('2.0'), LoadPhase.L3: Decimal('4.0')},
            loads=(),
        ),
        at=rig.clock(),
        thresholds=CONTROL.thresholds,
    )
    return rig


@pytest.fixture
def f_overloaded_rig(f_rig: GuardRig) -> GuardRig:
    _seconds(f_rig, 4, {LoadPhase.L2: '26.0'})
    return f_rig


@pytest.fixture
def f_rig_whose_l2_read_nineteen_amperes(f_rig: GuardRig) -> GuardRig:
    _seconds(f_rig, 3, {LoadPhase.L2: '19.0'})
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


def test_rise_no_load_explains_held_five_minutes_is_an_ev_charging_until_it_drops_back_unexplained(
    f_rig: GuardRig,
) -> None:
    for second in range(1, 361):
        _second(f_rig, {LoadPhase.L3: '20.0' if second in EV_CHARGING else '4.0'})

    assert f_rig.events == [
        EvChargingStarted(phase=LoadPhase.L3, amps=Decimal('16.0'), before=Decimal('4.0'), occurred_at=_at(306)),
        EvChargingStopped(phase=LoadPhase.L3, amps=Decimal('16.0'), before=Decimal('4.0'), occurred_at=_at(357)),
    ]


def test_phase_that_goes_silent_keeps_its_last_reading_however_long_it_stays_silent(
    f_rig_whose_l2_read_nineteen_amperes: GuardRig,
) -> None:
    _seconds_without(f_rig_whose_l2_read_nineteen_amperes, 180, LoadPhase.L2)

    state = f_rig_whose_l2_read_nineteen_amperes.book.state()
    assert (None if state is None else state.phases.silent_currents) == {LoadPhase.L2: Decimal('19.0')}


def test_estimate_takes_the_place_of_the_unclamped_phase_while_the_samples_keep_the_meter(
    f_rig_estimating_l2: GuardRig,
) -> None:
    _seconds(f_rig_estimating_l2, 2, WITHOUT_AN_L2_CLAMP)

    phases = _phases(f_rig_estimating_l2)
    assert (phases.currents, phases.samples[-1].amps, phases.estimate) == (
        {LoadPhase.L1: Decimal('8.0'), LoadPhase.L2: Decimal('24.02'), LoadPhase.L3: Decimal('4.0')},
        {LoadPhase.L1: Decimal('8.0'), LoadPhase.L2: Decimal('0.2'), LoadPhase.L3: Decimal('4.0')},
        PhaseEstimate(
            phase=LoadPhase.L2,
            amps=Decimal('24.02'),
            parts=(
                ESTIMATE_BASE,
                EstimatePart(load='boiler-2f', amps=Decimal('6.02'), basis=EstimateBasis.MEASURED),
                EstimatePart(load='bathroom-1f', amps=Decimal('16.0'), basis=EstimateBasis.DRAWING),
            ),
        ),
    )


@pytest.mark.parametrize(
    ('f_rig_with_a_convector', 'parts'),
    [
        (
            True,
            (ESTIMATE_BASE, EstimatePart(load='convector-office', amps=Decimal('8.7'), basis=EstimateBasis.DRAWING)),
        ),
        (False, (ESTIMATE_BASE,)),
    ],
    ids=['on_though_its_own_temperature_reads_above_its_setpoint', 'off'],
    indirect=['f_rig_with_a_convector'],
)
def test_estimate_counts_a_heat_source_at_its_rated_current_while_it_is_on(
    f_rig_with_a_convector: GuardRig,
    parts: tuple[EstimatePart, ...],
) -> None:
    _seconds(f_rig_with_a_convector, 2, WITHOUT_AN_L2_CLAMP)

    estimate = _phases(f_rig_with_a_convector).estimate
    assert (None if estimate is None else estimate.parts) == parts


def test_phase_is_measured_while_no_phase_is_estimated(f_rig_measuring_l2: GuardRig) -> None:
    _seconds(f_rig_measuring_l2, 2, WITHOUT_AN_L2_CLAMP)

    phases = _phases(f_rig_measuring_l2)
    assert (phases.currents[LoadPhase.L2], phases.estimate) == (Decimal('0.2'), None)


def test_estimated_phase_that_goes_silent_has_no_estimate_and_keeps_the_last_one_as_its_last_reading(
    f_rig_whose_l2_went_silent: GuardRig,
) -> None:
    phases = _phases(f_rig_whose_l2_went_silent)

    assert (LoadPhase.L2 in phases.currents, phases.estimate, phases.silent_currents) == (
        False,
        None,
        {LoadPhase.L2: Decimal('24.02')},
    )


def test_overload_by_the_estimate_is_journaled_as_estimated_and_raises_no_alarm(f_rig_estimating_l2: GuardRig) -> None:
    outputs = _seconds(f_rig_estimating_l2, 4, WITHOUT_AN_L2_CLAMP)

    watch = _phases(f_rig_estimating_l2).watches[LoadPhase.L2]
    assert (
        [output.overloaded for output in outputs],
        f_rig_estimating_l2.events,
        (watch.measured_above_since, watch.alarmed),
    ) == (
        [(), (), (), (LoadPhase.L2,)],
        [PhaseOverloaded(phase=LoadPhase.L2, amps=Decimal('24.02'), estimated=True, occurred_at=_at(4))],
        (None, False),
    )


def test_estimate_counts_a_granted_start_until_a_poll_after_its_command_reads_the_load(
    f_rig_whose_bathroom_floor_was_granted_a_start: GuardRig,
) -> None:
    rig = f_rig_whose_bathroom_floor_was_granted_a_start
    rig.thermostats.record_state('thermostat-bathroom-1f', _bathroom(heating=False, second=1))
    _seconds(rig, 1, WITHOUT_AN_L2_CLAMP)
    before_its_command = _phases(rig).estimate
    rig.book.note_started('bathroom-1f', rig.clock())
    _seconds(rig, 1, WITHOUT_AN_L2_CLAMP)
    after_its_command = _phases(rig).estimate
    rig.thermostats.record_state('thermostat-bathroom-1f', _bathroom(heating=False, second=3))
    _seconds(rig, 1, WITHOUT_AN_L2_CLAMP)
    started = PhaseEstimate(
        phase=LoadPhase.L2,
        amps=Decimal('8.00'),
        parts=(
            ESTIMATE_BASE,
            EstimatePart(load='bathroom-1f', amps=Decimal('6.0'), basis=EstimateBasis.STARTED),
        ),
    )

    assert (before_its_command, after_its_command, _phases(rig).estimate) == (
        started,
        started,
        PhaseEstimate(phase=LoadPhase.L2, amps=Decimal('2.00'), parts=(ESTIMATE_BASE,)),
    )


def test_learning_follows_the_meter_of_the_estimated_phase(f_rig_estimating_an_empty_l2: GuardRig) -> None:
    outputs = _switch_boiler_three_times(f_rig_estimating_an_empty_l2)

    assert (
        [second for second, output in enumerate(outputs, start=1) if output.learned],
        f_rig_estimating_an_empty_l2.loads.writes,
    ) == ([135], [('save_learned', 'boiler-1f', LearnedPhase(phase=LoadPhase.L2, amps=Decimal('6.0'), samples=3))])


def test_ev_detection_follows_the_meter_of_the_estimated_phase(f_rig_estimating_an_empty_l2: GuardRig) -> None:
    for second in range(1, 361):
        _second(f_rig_estimating_an_empty_l2, {LoadPhase.L2: '20.0' if second in EV_CHARGING else '4.0'})

    assert f_rig_estimating_an_empty_l2.events == [
        EvChargingStarted(phase=LoadPhase.L2, amps=Decimal('16.0'), before=Decimal('4.0'), occurred_at=_at(306)),
        EvChargingStopped(phase=LoadPhase.L2, amps=Decimal('16.0'), before=Decimal('4.0'), occurred_at=_at(357)),
    ]
