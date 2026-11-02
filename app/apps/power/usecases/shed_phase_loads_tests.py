from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pytest_django import DjangoAssertNumQueries

from apps.core.domain.errors import DomainError
from apps.core.domain.events import DomainEvent, EventDispatcher
from apps.core.stubs import MonotonicClockStub
from apps.devices.domain.errors import DeviceUnavailableError
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.domain.readings import Phase, PlugState, ThermostatMode, ThermostatState
from apps.devices.factories import DeviceFactory
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.phase_meter_store import PhaseMeterStore
from apps.devices.services.state_store import PlugStateStore, ThermostatStateStore
from apps.devices.stubs import SwitchablePlugProviderStub, SwitchablePlugStub
from apps.power.domain.control import LoadControl, PhaseThresholds
from apps.power.domain.events import (
    EvChargingStarted,
    EvChargingStopped,
    LoadCommand,
    LoadCommandFailed,
    LoadPhaseLearned,
    LoadRestored,
    LoadRestoredDryRun,
    LoadShed,
    LoadShedDryRun,
    LoadShedOverridden,
    PhaseCalmed,
    PhaseOverloaded,
    RestoreCause,
)
from apps.power.domain.loads import LiveShed, LoadKind, LoadPhase, LoadRecord, ShedReason
from apps.power.factories import PlugLoadFactory, ZoneLoadFactory
from apps.power.repositories.load import LoadRepository
from apps.power.repositories.load_settings import LoadSettingsRepository
from apps.power.services.guard_book import GuardBook
from apps.power.services.load_switcher import LoadSwitcher
from apps.power.stubs import DeviceDirectoryStub, LoadSettingsStoreStub, LoadStoreStub, ZoneShutoffStub
from apps.power.usecases.shed_phase_loads import (
    ShedPhaseLoadsInputDTO,
    ShedPhaseLoadsOutputDTO,
    ShedPhaseLoadsUseCase,
)
from apps.power.usecases.watch_phases import WatchPhasesInputDTO, WatchPhasesUseCase

NOW = datetime(2026, 11, 2, 18, 40, tzinfo=UTC)
START = 1000.0
METER = 'phase-meter'
UNAVAILABLE_AFTER = timedelta(minutes=10)
DRY_RUN_CONTROL = LoadControl(
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
LIVE_CONTROL = replace(DRY_RUN_CONTROL, live=True)
BOILER = LoadRecord(
    slug='boiler-1f',
    name='Бойлер 1 этаж',
    kind=LoadKind.PLUG,
    zone_slug=None,
    device_slug='plug-boiler-1f',
    priority=1,
    phase=LoadPhase.L2,
    nominal_amps=Decimal('6.0'),
    learned=None,
    live=False,
    shed=None,
)
KITCHEN = LoadRecord(
    slug='kitchen',
    name='Кухня',
    kind=LoadKind.ZONE,
    zone_slug='kitchen',
    device_slug='thermostat-kitchen',
    priority=5,
    phase=LoadPhase.L2,
    nominal_amps=Decimal('8.0'),
    learned=None,
    live=False,
    shed=None,
)
LIVING_ROOM = replace(
    KITCHEN,
    slug='living-room',
    name='Гостиная',
    zone_slug='living-room',
    device_slug='thermostat-living-room',
    priority=7,
)
BATHROOM = replace(
    KITCHEN,
    slug='bathroom-1f',
    name='Санузел 1 этаж',
    zone_slug='bathroom-1f',
    device_slug='thermostat-bathroom-1f',
    priority=3,
    phase=LoadPhase.UNKNOWN,
    nominal_amps=None,
)
LIVE_ZONES = frozenset({'kitchen', 'living-room', 'bathroom-1f'})
STORED_SHED = LiveShed(
    at=NOW - timedelta(hours=1),
    reason=ShedReason.OVERLOAD,
    phase=LoadPhase.L2,
    was_on=True,
)
POWER_EVENTS = (
    PhaseOverloaded,
    PhaseCalmed,
    LoadShed,
    LoadShedDryRun,
    LoadRestored,
    LoadRestoredDryRun,
    LoadShedOverridden,
    LoadCommandFailed,
    LoadPhaseLearned,
    EvChargingStarted,
    EvChargingStopped,
)
NOTHING = ShedPhaseLoadsOutputDTO(shed=(), restored=())


@dataclass(frozen=True, kw_only=True)
class Guard:
    clock: MonotonicClockStub
    meter: PhaseMeterStore
    watch: WatchPhasesUseCase
    shed: ShedPhaseLoadsUseCase


@dataclass(frozen=True, kw_only=True)
class House:
    guard: Guard
    plugs: PlugStateStore
    loads: LoadStoreStub
    load_settings: LoadSettingsStoreStub
    boiler: SwitchablePlugStub
    zones: ZoneShutoffStub
    events: list[DomainEvent]


def _moment(second: int) -> datetime:
    return NOW + timedelta(seconds=second)


def _plug(*, on: bool, second: int) -> PlugState:
    return PlugState(
        on=on,
        power_watts=Decimal('1305.4') if on else Decimal(0),
        current_amps=Decimal('6.02') if on else Decimal(0),
        voltage_volts=Decimal('216.8'),
        read_at=_moment(second),
    )


def _heating() -> ThermostatState:
    return ThermostatState(
        power=True,
        mode=ThermostatMode.HOLD,
        setpoint=Decimal('28.0'),
        floor_temperature=Decimal('24.5'),
        heating=True,
        read_at=NOW,
    )


def _live(*records: LoadRecord) -> list[LoadRecord]:
    return [replace(record, live=True) for record in records]


def _house(
    records: list[LoadRecord],
    control: LoadControl,
    live_zones: frozenset[str],
    *,
    dry_run: bool = False,
    boiler_on: bool = True,
    boiler_error: DomainError | None = None,
) -> House:
    clock = MonotonicClockStub(now=START)
    events: list[DomainEvent] = []
    dispatcher = EventDispatcher()
    for event_type in POWER_EVENTS:
        dispatcher.register(event_type, events.append)
    meter = PhaseMeterStore(stale_after=timedelta(seconds=10), unavailable_after=UNAVAILABLE_AFTER, clock=clock)
    plugs = PlugStateStore(unavailable_after=UNAVAILABLE_AFTER, clock=clock)
    thermostats = ThermostatStateStore(unavailable_after=UNAVAILABLE_AFTER, clock=clock)
    for record in records:
        if record.kind is LoadKind.PLUG:
            plugs.record_state(record.device_slug, _plug(on=boiler_on, second=0))
        else:
            thermostats.record_state(record.device_slug, _heating())
    loads = LoadStoreStub(records=records, live_zones=live_zones)
    load_settings = LoadSettingsStoreStub(control=control)
    boiler = SwitchablePlugStub(state=None, error=boiler_error)
    zones = ZoneShutoffStub(error=None)
    book = GuardBook()
    return House(
        guard=Guard(
            clock=clock,
            meter=meter,
            watch=WatchPhasesUseCase(
                loads=loads,
                load_settings=load_settings,
                devices=DeviceDirectoryStub(slugs={DeviceKind.PHASE_METER: [METER]}),
                meter=meter,
                thermostats=thermostats,
                plugs=plugs,
                book=book,
                clock=clock,
                dispatcher=dispatcher,
            ),
            shed=ShedPhaseLoadsUseCase(
                loads=loads,
                switcher=LoadSwitcher(plugs=SwitchablePlugProviderStub(plugs={'plug-boiler-1f': boiler}), zones=zones),
                book=book,
                clock=clock,
                dry_run=dry_run,
                dispatcher=dispatcher,
            ),
        ),
        plugs=plugs,
        loads=loads,
        load_settings=load_settings,
        boiler=boiler,
        zones=zones,
        events=events,
    )


def _seconds(guard: Guard, count: int, l2: str | None) -> list[ShedPhaseLoadsOutputDTO]:
    outputs: list[ShedPhaseLoadsOutputDTO] = []
    for _ in range(count):
        guard.clock.advance(1)
        now = _moment(int(guard.clock() - START))
        if l2 is not None:
            for phase, amps in ((Phase.A, '8.0'), (Phase.B, l2), (Phase.C, '4.0')):
                guard.meter.record_current(METER, phase, Decimal(amps), now)
        guard.watch.execute(WatchPhasesInputDTO(now=now))
        outputs.append(guard.shed.execute(ShedPhaseLoadsInputDTO(now=now)))
    return outputs


def _shed_at(outputs: list[ShedPhaseLoadsOutputDTO], first: int) -> list[tuple[int, tuple[str, ...]]]:
    return [(second, output.shed) for second, output in enumerate(outputs, start=first) if output.shed]


def _restored_at(outputs: list[ShedPhaseLoadsOutputDTO], first: int) -> list[tuple[int, tuple[str, ...]]]:
    return [(second, output.restored) for second, output in enumerate(outputs, start=first) if output.restored]


def _overload(second: int, amps: str) -> PhaseOverloaded:
    return PhaseOverloaded(phase=LoadPhase.L2, amps=Decimal(amps), occurred_at=_moment(second))


def _load_off(house: House) -> None:
    house.loads.edit([replace(record, live=False) for record in house.loads.records], frozenset())


def _load_management_off(house: House) -> None:
    house.load_settings.edit(replace(LIVE_CONTROL, live=False))


def _load_on(house: House) -> None:
    house.loads.edit([replace(record, live=True) for record in house.loads.records], frozenset())


@pytest.fixture
def f_dry_run_house() -> House:
    return _house([BOILER, KITCHEN, LIVING_ROOM], DRY_RUN_CONTROL, frozenset())


@pytest.fixture
def f_dry_run_house_after_a_shed(f_dry_run_house: House) -> House:
    _seconds(f_dry_run_house.guard, 4, '26.0')
    return f_dry_run_house


@pytest.fixture
def f_live_house() -> House:
    return _house(_live(BOILER, KITCHEN, LIVING_ROOM), LIVE_CONTROL, LIVE_ZONES)


@pytest.fixture
def f_live_house_after_a_shed(f_live_house: House) -> House:
    _seconds(f_live_house.guard, 4, '26.0')
    return f_live_house


@pytest.fixture
def f_live_house_after_two_sheds(f_live_house: House) -> House:
    _seconds(f_live_house.guard, 5, '40.0')
    return f_live_house


@pytest.fixture
def f_boiler_shed_live() -> House:
    house = _house(_live(BOILER), LIVE_CONTROL, frozenset())
    _seconds(house.guard, 4, '26.0')
    return house


@pytest.fixture
def f_boiler_shed_in_dry_run() -> House:
    house = _house([BOILER], LIVE_CONTROL, frozenset())
    _seconds(house.guard, 4, '26.0')
    return house


@pytest.fixture
def f_unknown_phase_house() -> House:
    return _house([BOILER, BATHROOM], DRY_RUN_CONTROL, frozenset())


@pytest.fixture
def f_restarted_house() -> House:
    return _house([replace(BOILER, live=True, shed=STORED_SHED)], LIVE_CONTROL, frozenset(), boiler_on=False)


@pytest.fixture
def f_teplo_dry_run_house() -> House:
    return _house(
        [replace(BOILER, live=True, shed=STORED_SHED), replace(KITCHEN, live=True)],
        LIVE_CONTROL,
        LIVE_ZONES,
        dry_run=True,
    )


@pytest.fixture
def f_zone_without_live_control_house() -> House:
    return _house(_live(KITCHEN), LIVE_CONTROL, frozenset())


@pytest.fixture
def f_unreachable_plug_house() -> House:
    return _house(
        _live(BOILER, KITCHEN),
        LIVE_CONTROL,
        LIVE_ZONES,
        boiler_error=DeviceUnavailableError('timed out'),
    )


@pytest.fixture
def f_live_floors_house() -> House:
    return _house(_live(KITCHEN, LIVING_ROOM), LIVE_CONTROL, LIVE_ZONES)


@pytest.fixture
def f_dry_run_floors_house() -> House:
    return _house([KITCHEN, LIVING_ROOM], DRY_RUN_CONTROL, frozenset())


@pytest.fixture
def f_dry_run_floors_after_the_meter_reported(f_dry_run_floors_house: House) -> House:
    _seconds(f_dry_run_floors_house.guard, 3, '10.0')
    return f_dry_run_floors_house


@pytest.fixture
def f_guard_after_its_first_second() -> Guard:
    PlugLoadFactory.create(slug='boiler-1f', device__slug='plug-boiler-1f', priority=1, live=True)
    ZoneLoadFactory.create(
        slug='kitchen',
        zone__slug='kitchen',
        zone__thermostat__slug='thermostat-kitchen',
        zone__live_control=True,
        priority=5,
        live=True,
    )
    DeviceFactory.create(slug=METER, kind=DeviceKind.PHASE_METER, driver=DeviceDriver.ESPHOME_PHASE_METER)
    clock = MonotonicClockStub(now=START)
    meter = PhaseMeterStore(stale_after=timedelta(seconds=10), unavailable_after=UNAVAILABLE_AFTER, clock=clock)
    plugs = PlugStateStore(unavailable_after=UNAVAILABLE_AFTER, clock=clock)
    plugs.record_state('plug-boiler-1f', _plug(on=True, second=0))
    thermostats = ThermostatStateStore(unavailable_after=UNAVAILABLE_AFTER, clock=clock)
    thermostats.record_state('thermostat-kitchen', _heating())
    book = GuardBook()
    guard = Guard(
        clock=clock,
        meter=meter,
        watch=WatchPhasesUseCase(
            loads=LoadRepository(),
            load_settings=LoadSettingsRepository(),
            devices=DeviceRepository(),
            meter=meter,
            thermostats=thermostats,
            plugs=plugs,
            book=book,
            clock=clock,
            dispatcher=EventDispatcher(),
        ),
        shed=ShedPhaseLoadsUseCase(
            loads=LoadRepository(),
            switcher=LoadSwitcher(plugs=SwitchablePlugProviderStub(plugs={}), zones=ZoneShutoffStub(error=None)),
            book=book,
            clock=clock,
            dry_run=False,
            dispatcher=EventDispatcher(),
        ),
    )
    _seconds(guard, 1, '12.0')
    return guard


def test_three_seconds_above_the_shed_threshold_shed_nothing(f_dry_run_house: House) -> None:
    outputs = _seconds(f_dry_run_house.guard, 3, '26.0')

    assert (outputs, f_dry_run_house.events) == ([NOTHING] * 3, [])


def test_overload_in_dry_run_journals_the_shed_after_the_overload_and_switches_nothing(
    f_dry_run_house: House,
) -> None:
    outputs = _seconds(f_dry_run_house.guard, 4, '26.0')

    assert (
        outputs[-1],
        f_dry_run_house.events,
        f_dry_run_house.boiler.switches,
        f_dry_run_house.zones.switched_off,
        f_dry_run_house.loads.writes,
    ) == (
        ShedPhaseLoadsOutputDTO(shed=('boiler-1f',), restored=()),
        [
            _overload(4, '26.0'),
            LoadShedDryRun(
                load='boiler-1f',
                name='Бойлер 1 этаж',
                kind=LoadKind.PLUG,
                phase=LoadPhase.L2,
                amps=Decimal('26.0'),
                load_amps=Decimal('6.0'),
                reason=ShedReason.OVERLOAD,
                occurred_at=_moment(4),
            ),
        ],
        [],
        [],
        [],
    )


def test_dry_run_shed_relieves_its_phase_until_it_is_restored(f_dry_run_house_after_a_shed: House) -> None:
    relieved = _seconds(f_dry_run_house_after_a_shed.guard, 30, '26.0')
    heavier = _seconds(f_dry_run_house_after_a_shed.guard, 1, '28.0')

    assert (relieved, heavier) == ([NOTHING] * 30, [ShedPhaseLoadsOutputDTO(shed=('kitchen',), restored=())])


def test_live_overload_switches_the_plug_off_and_stores_the_shed(f_live_house: House) -> None:
    _seconds(f_live_house.guard, 4, '26.0')

    assert (f_live_house.boiler.switches, f_live_house.loads.writes, f_live_house.events) == (
        [False],
        [
            (
                'mark_shed',
                'boiler-1f',
                LiveShed(at=_moment(4), reason=ShedReason.OVERLOAD, phase=LoadPhase.L2, was_on=True),
            ),
        ],
        [
            _overload(4, '26.0'),
            LoadShed(
                load='boiler-1f',
                name='Бойлер 1 этаж',
                kind=LoadKind.PLUG,
                phase=LoadPhase.L2,
                amps=Decimal('26.0'),
                load_amps=Decimal('6.0'),
                reason=ShedReason.OVERLOAD,
                occurred_at=_moment(4),
            ),
        ],
    )


def test_live_shed_relieves_its_phase_only_while_it_settles(f_live_house_after_a_shed: House) -> None:
    outputs = _seconds(f_live_house_after_a_shed.guard, 5, '26.0')

    assert (_shed_at(outputs, 5), f_live_house_after_a_shed.zones.switched_off) == (
        [(9, ('kitchen',))],
        [('kitchen', 'thermostat-kitchen', _moment(9))],
    )


def test_one_load_is_shed_a_second(f_live_house: House) -> None:
    outputs = _seconds(f_live_house.guard, 6, '40.0')

    assert _shed_at(outputs, 1) == [(4, ('boiler-1f',)), (5, ('kitchen',)), (6, ('living-room',))]


def test_load_of_unknown_phase_is_shed_for_the_phase_once_none_of_its_own_draws(f_unknown_phase_house: House) -> None:
    outputs = _seconds(f_unknown_phase_house.guard, 5, '30.0')

    assert (_shed_at(outputs, 1), f_unknown_phase_house.events[-1]) == (
        [(4, ('boiler-1f',)), (5, ('bathroom-1f',))],
        LoadShedDryRun(
            load='bathroom-1f',
            name='Санузел 1 этаж',
            kind=LoadKind.ZONE,
            phase=LoadPhase.L2,
            amps=Decimal('30.0'),
            load_amps=Decimal('6.0'),
            reason=ShedReason.OVERLOAD,
            occurred_at=_moment(5),
        ),
    )


def test_restores_wait_two_minutes_of_calm_and_return_one_load_a_minute_most_important_first(
    f_live_house_after_two_sheds: House,
) -> None:
    outputs = _seconds(f_live_house_after_two_sheds.guard, 181, '10.0')

    assert (
        _restored_at(outputs, 6),
        f_live_house_after_two_sheds.boiler.switches,
        f_live_house_after_two_sheds.loads.writes[2:],
        f_live_house_after_two_sheds.events[3:],
    ) == (
        [(126, ('kitchen',)), (186, ('boiler-1f',))],
        [False, True],
        [('clear_shed', 'kitchen', None), ('clear_shed', 'boiler-1f', None)],
        [
            PhaseCalmed(phase=LoadPhase.L2, amps=Decimal('10.0'), occurred_at=_moment(126)),
            LoadRestored(
                load='kitchen',
                name='Кухня',
                kind=LoadKind.ZONE,
                phase=LoadPhase.L2,
                amps=Decimal('10.0'),
                load_amps=Decimal('8.0'),
                reason=ShedReason.OVERLOAD,
                cause=RestoreCause.CALM,
                occurred_at=_moment(126),
            ),
            LoadRestored(
                load='boiler-1f',
                name='Бойлер 1 этаж',
                kind=LoadKind.PLUG,
                phase=LoadPhase.L2,
                amps=Decimal('10.0'),
                load_amps=Decimal('6.0'),
                reason=ShedReason.OVERLOAD,
                cause=RestoreCause.CALM,
                occurred_at=_moment(186),
            ),
        ],
    )


def test_restore_waits_for_room_under_the_start_ceiling(f_live_house_after_two_sheds: House) -> None:
    crowded = _seconds(f_live_house_after_two_sheds.guard, 181, '16.0')
    roomy = _seconds(f_live_house_after_two_sheds.guard, 1, '12.0')

    assert (_restored_at(crowded, 6), _restored_at(roomy, 187)) == ([], [(187, ('kitchen',))])


def test_restore_in_dry_run_journals_what_it_would_switch_back(f_dry_run_house_after_a_shed: House) -> None:
    outputs = _seconds(f_dry_run_house_after_a_shed.guard, 121, '10.0')

    assert (
        _restored_at(outputs, 5),
        f_dry_run_house_after_a_shed.boiler.switches,
        f_dry_run_house_after_a_shed.events[2:],
    ) == (
        [(125, ('boiler-1f',))],
        [],
        [
            PhaseCalmed(phase=LoadPhase.L2, amps=Decimal('10.0'), occurred_at=_moment(125)),
            LoadRestoredDryRun(
                load='boiler-1f',
                name='Бойлер 1 этаж',
                kind=LoadKind.PLUG,
                phase=LoadPhase.L2,
                amps=Decimal('10.0'),
                load_amps=Decimal('6.0'),
                reason=ShedReason.OVERLOAD,
                cause=RestoreCause.CALM,
                occurred_at=_moment(125),
            ),
        ],
    )


def test_owner_switching_a_shed_plug_back_on_takes_it_over_for_the_overload(f_boiler_shed_live: House) -> None:
    _seconds(f_boiler_shed_live.guard, 5, '26.0')
    f_boiler_shed_live.plugs.record_state('plug-boiler-1f', _plug(on=True, second=9))
    outputs = _seconds(f_boiler_shed_live.guard, 21, '26.0')

    assert (
        outputs[0],
        f_boiler_shed_live.boiler.switches,
        f_boiler_shed_live.loads.writes[1:],
        f_boiler_shed_live.events[2:],
    ) == (
        ShedPhaseLoadsOutputDTO(shed=(), restored=('boiler-1f',)),
        [False],
        [('clear_shed', 'boiler-1f', None)],
        [LoadShedOverridden(load='boiler-1f', name='Бойлер 1 этаж', phase=LoadPhase.L2, occurred_at=_moment(10))],
    )


def test_shed_stored_before_a_restart_is_restored_by_the_same_rule(f_restarted_house: House) -> None:
    outputs = _seconds(f_restarted_house.guard, 121, '10.0')

    assert (
        _restored_at(outputs, 1),
        f_restarted_house.boiler.switches,
        f_restarted_house.loads.writes,
        f_restarted_house.events,
    ) == (
        [(121, ('boiler-1f',))],
        [True],
        [('clear_shed', 'boiler-1f', None)],
        [
            LoadRestored(
                load='boiler-1f',
                name='Бойлер 1 этаж',
                kind=LoadKind.PLUG,
                phase=LoadPhase.L2,
                amps=Decimal('10.0'),
                load_amps=Decimal('6.0'),
                reason=ShedReason.OVERLOAD,
                cause=RestoreCause.CALM,
                occurred_at=_moment(121),
            ),
        ],
    )


def test_teplo_in_dry_run_switches_nothing_and_leaves_a_stored_shed_alone(f_teplo_dry_run_house: House) -> None:
    _seconds(f_teplo_dry_run_house.guard, 4, '26.0')

    assert (
        f_teplo_dry_run_house.events,
        f_teplo_dry_run_house.boiler.switches,
        f_teplo_dry_run_house.zones.switched_off,
        f_teplo_dry_run_house.loads.writes,
    ) == (
        [
            _overload(4, '26.0'),
            LoadShedDryRun(
                load='boiler-1f',
                name='Бойлер 1 этаж',
                kind=LoadKind.PLUG,
                phase=LoadPhase.L2,
                amps=Decimal('26.0'),
                load_amps=Decimal('6.0'),
                reason=ShedReason.OVERLOAD,
                occurred_at=_moment(4),
            ),
        ],
        [],
        [],
        [],
    )


@pytest.mark.parametrize('switch_off', [_load_off, _load_management_off], ids=['load', 'load_management'])
def test_gate_switched_off_releases_the_live_shed_within_a_minute(
    f_boiler_shed_live: House,
    switch_off: Callable[[House], None],
) -> None:
    _seconds(f_boiler_shed_live.guard, 1, '15.0')
    switch_off(f_boiler_shed_live)
    outputs = _seconds(f_boiler_shed_live.guard, 56, '15.0')

    assert (
        _restored_at(outputs, 6),
        f_boiler_shed_live.boiler.switches,
        f_boiler_shed_live.loads.writes[1:],
        f_boiler_shed_live.events[2:],
    ) == (
        [(61, ('boiler-1f',))],
        [False, True],
        [('clear_shed', 'boiler-1f', None)],
        [
            LoadRestored(
                load='boiler-1f',
                name='Бойлер 1 этаж',
                kind=LoadKind.PLUG,
                phase=LoadPhase.L2,
                amps=Decimal('15.0'),
                load_amps=Decimal('6.0'),
                reason=ShedReason.OVERLOAD,
                cause=RestoreCause.RELEASED,
                occurred_at=_moment(61),
            ),
        ],
    )


def test_dry_run_shed_of_a_load_switched_live_is_dropped_without_a_switch(f_boiler_shed_in_dry_run: House) -> None:
    _seconds(f_boiler_shed_in_dry_run.guard, 1, '15.0')
    _load_on(f_boiler_shed_in_dry_run)
    outputs = _seconds(f_boiler_shed_in_dry_run.guard, 56, '15.0')

    assert (
        _restored_at(outputs, 6),
        f_boiler_shed_in_dry_run.boiler.switches,
        f_boiler_shed_in_dry_run.events[2:],
    ) == (
        [(61, ('boiler-1f',))],
        [],
        [
            LoadRestoredDryRun(
                load='boiler-1f',
                name='Бойлер 1 этаж',
                kind=LoadKind.PLUG,
                phase=LoadPhase.L2,
                amps=Decimal('15.0'),
                load_amps=Decimal('6.0'),
                reason=ShedReason.OVERLOAD,
                cause=RestoreCause.RELEASED,
                occurred_at=_moment(61),
            ),
        ],
    )


def test_zone_without_live_control_is_shed_in_dry_run(f_zone_without_live_control_house: House) -> None:
    _seconds(f_zone_without_live_control_house.guard, 4, '26.0')

    assert (f_zone_without_live_control_house.events[-1], f_zone_without_live_control_house.zones.switched_off) == (
        LoadShedDryRun(
            load='kitchen',
            name='Кухня',
            kind=LoadKind.ZONE,
            phase=LoadPhase.L2,
            amps=Decimal('26.0'),
            load_amps=Decimal('8.0'),
            reason=ShedReason.OVERLOAD,
            occurred_at=_moment(4),
        ),
        [],
    )


def test_failed_shed_is_journaled_and_the_next_load_is_shed_a_second_later(f_unreachable_plug_house: House) -> None:
    outputs = _seconds(f_unreachable_plug_house.guard, 5, '26.0')

    assert (
        _shed_at(outputs, 1),
        f_unreachable_plug_house.events[1:],
        f_unreachable_plug_house.zones.switched_off,
    ) == (
        [(5, ('kitchen',))],
        [
            LoadCommandFailed(
                load='boiler-1f',
                name='Бойлер 1 этаж',
                command=LoadCommand.SHED,
                error='timed out',
                error_code='device_unavailable',
                occurred_at=_moment(4),
            ),
            LoadShed(
                load='kitchen',
                name='Кухня',
                kind=LoadKind.ZONE,
                phase=LoadPhase.L2,
                amps=Decimal('26.0'),
                load_amps=Decimal('8.0'),
                reason=ShedReason.OVERLOAD,
                occurred_at=_moment(5),
            ),
        ],
        [('kitchen', 'thermostat-kitchen', _moment(5))],
    )


def test_live_house_without_a_meter_keeps_one_floor_a_phase_a_minute_after_the_start(
    f_live_floors_house: House,
) -> None:
    outputs = _seconds(f_live_floors_house.guard, 62, None)

    assert (
        _shed_at(outputs, 1),
        f_live_floors_house.zones.switched_off,
        f_live_floors_house.loads.writes,
        f_live_floors_house.events,
    ) == (
        [(61, ('kitchen',))],
        [('kitchen', 'thermostat-kitchen', _moment(61))],
        [
            (
                'mark_shed',
                'kitchen',
                LiveShed(at=_moment(61), reason=ShedReason.METER_LOST, phase=LoadPhase.L2, was_on=None),
            ),
        ],
        [
            LoadShed(
                load='kitchen',
                name='Кухня',
                kind=LoadKind.ZONE,
                phase=LoadPhase.L2,
                amps=None,
                load_amps=Decimal('8.0'),
                reason=ShedReason.METER_LOST,
                occurred_at=_moment(61),
            ),
        ],
    )


def test_dry_run_stays_quiet_about_a_meter_that_never_reported(f_dry_run_floors_house: House) -> None:
    outputs = _seconds(f_dry_run_floors_house.guard, 120, None)

    assert (outputs, f_dry_run_floors_house.events) == ([NOTHING] * 120, [])


def test_dry_run_journals_the_limp_house_once_a_meter_that_reported_is_lost_and_its_release(
    f_dry_run_floors_after_the_meter_reported: House,
) -> None:
    silent = _seconds(f_dry_run_floors_after_the_meter_reported.guard, 77, None)
    back = _seconds(f_dry_run_floors_after_the_meter_reported.guard, 121, '10.0')

    assert (_shed_at(silent, 4), _restored_at(back, 81), f_dry_run_floors_after_the_meter_reported.events) == (
        [(74, ('kitchen',))],
        [(201, ('kitchen',))],
        [
            LoadShedDryRun(
                load='kitchen',
                name='Кухня',
                kind=LoadKind.ZONE,
                phase=LoadPhase.L2,
                amps=None,
                load_amps=Decimal('8.0'),
                reason=ShedReason.METER_LOST,
                occurred_at=_moment(74),
            ),
            LoadRestoredDryRun(
                load='kitchen',
                name='Кухня',
                kind=LoadKind.ZONE,
                phase=LoadPhase.L2,
                amps=Decimal('10.0'),
                load_amps=Decimal('8.0'),
                reason=ShedReason.METER_LOST,
                cause=RestoreCause.CALM,
                occurred_at=_moment(201),
            ),
        ],
    )


@pytest.mark.django_db
def test_ordinary_second_neither_reads_nor_writes_the_database(
    f_guard_after_its_first_second: Guard,
    django_assert_num_queries: DjangoAssertNumQueries,
) -> None:
    with django_assert_num_queries(0):
        outputs = _seconds(f_guard_after_its_first_second, 1, '12.0')

    assert outputs == [NOTHING]
