from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from django.db import OperationalError
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
from apps.power.domain.load_status import LoadStatus
from apps.power.domain.loads import LiveShed, LoadKind, LoadPhase, LoadRecord, ShedReason
from apps.power.domain.meter_watch import MeterCondition
from apps.power.domain.shedding import UnconfirmedShed
from apps.power.domain.view import GuardView
from apps.power.factories import PlugLoadFactory, ZoneLoadFactory
from apps.power.repositories.load import LoadRepository
from apps.power.repositories.load_settings import LoadSettingsRepository
from apps.power.services.guard_book import GuardBook
from apps.power.services.load_switcher import LoadSwitcher
from apps.power.stubs import DeviceDirectoryStub, LoadSettingsStoreStub, LoadStoreStub, ZoneSwitchStub
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
BOILER_2F = replace(BOILER, slug='boiler-2f', name='Бойлер 2 этаж', device_slug='plug-boiler-2f', priority=2)
LIVE_ZONES = frozenset({'kitchen', 'living-room', 'bathroom-1f'})
KITCHEN_START = LoadStatus(
    slug='kitchen',
    kind=LoadKind.ZONE,
    priority=5,
    phase=LoadPhase.L2,
    amps=Decimal('8.0'),
    available=True,
    drawing=False,
    shed=False,
)
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
PLUG_POLL_SECONDS = 30


@dataclass(frozen=True, kw_only=True)
class Guard:
    clock: MonotonicClockStub
    meter: PhaseMeterStore
    watch: WatchPhasesUseCase
    shed: ShedPhaseLoadsUseCase


@dataclass(frozen=True, kw_only=True)
class House:
    guard: Guard
    book: GuardBook
    plugs: PlugStateStore
    thermostats: ThermostatStateStore
    loads: LoadStoreStub
    load_settings: LoadSettingsStoreStub
    boiler: SwitchablePlugStub
    plug_provider: SwitchablePlugProviderStub
    zones: ZoneSwitchStub
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
    write_errors: tuple[Exception, ...] = (),
    zone_stamped: bool = True,
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
    loads = LoadStoreStub(records=records, live_zones=live_zones, write_errors=write_errors)
    load_settings = LoadSettingsStoreStub(control=control)
    boiler = SwitchablePlugStub(state=None, error=boiler_error)
    plug_provider = SwitchablePlugProviderStub(plugs={'plug-boiler-1f': boiler})
    zones = ZoneSwitchStub(error=None, stamped=zone_stamped)
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
                switcher=LoadSwitcher(plugs=plug_provider, zones=zones),
                book=book,
                clock=clock,
                dry_run=dry_run,
                dispatcher=dispatcher,
            ),
        ),
        book=book,
        plugs=plugs,
        thermostats=thermostats,
        loads=loads,
        load_settings=load_settings,
        boiler=boiler,
        plug_provider=plug_provider,
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


def _polled_seconds(house: House, count: int, l2: str) -> list[ShedPhaseLoadsOutputDTO]:
    outputs: list[ShedPhaseLoadsOutputDTO] = []
    for _ in range(count):
        second = int(house.guard.clock() - START) + 1
        if second % PLUG_POLL_SECONDS == 0:
            for record in house.loads.records:
                if record.kind is LoadKind.PLUG:
                    _plug_read_off(house, record.device_slug, second)
        outputs.extend(_seconds(house.guard, 1, l2))
    return outputs


def _pause(guard: Guard, seconds: int) -> None:
    guard.clock.advance(seconds)


def _shed_at(outputs: list[ShedPhaseLoadsOutputDTO], first: int) -> list[tuple[int, tuple[str, ...]]]:
    return [(second, output.shed) for second, output in enumerate(outputs, start=first) if output.shed]


def _restored_at(outputs: list[ShedPhaseLoadsOutputDTO], first: int) -> list[tuple[int, tuple[str, ...]]]:
    return [(second, output.restored) for second, output in enumerate(outputs, start=first) if output.restored]


def _overload(second: int, amps: str) -> PhaseOverloaded:
    return PhaseOverloaded(phase=LoadPhase.L2, amps=Decimal(amps), occurred_at=_moment(second))


def _unswitchable_restore(second: int) -> LoadCommandFailed:
    return LoadCommandFailed(
        load='boiler-2f',
        name='Бойлер 2 этаж',
        command=LoadCommand.RESTORE,
        error='plug plug-boiler-2f cannot be switched: no switching driver',
        error_code='plug_not_switchable',
        occurred_at=_moment(second),
    )


def _failed_commands(house: House) -> dict[str, float]:
    state = house.book.state()
    return {} if state is None else dict(state.shedding.failed)


def _unconfirmed_sheds(house: House) -> dict[str, UnconfirmedShed]:
    state = house.book.state()
    return {} if state is None else dict(state.shedding.unconfirmed)


def _load_off(house: House) -> None:
    house.loads.edit([replace(record, live=False) for record in house.loads.records], frozenset())


def _load_management_off(house: House) -> None:
    house.load_settings.edit(replace(LIVE_CONTROL, live=False))


def _load_on(house: House) -> None:
    house.loads.edit([replace(record, live=True) for record in house.loads.records], frozenset())


def _zone_live_control_off(house: House) -> None:
    house.loads.edit(house.loads.records, frozenset())


def _zone_load_off(house: House) -> None:
    house.loads.edit([replace(record, live=False) for record in house.loads.records], LIVE_ZONES)


def _plug_read_off(house: House, device: str, second: int) -> None:
    house.plugs.record_state(device, _plug(on=False, second=second))


def _thermostat_read_off(house: House, device: str, second: int) -> None:
    house.thermostats.record_state(
        device,
        ThermostatState(
            power=False,
            mode=ThermostatMode.HOLD,
            setpoint=Decimal('28.0'),
            floor_temperature=Decimal('24.5'),
            heating=False,
            read_at=_moment(second),
        ),
    )


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
def f_live_house_whose_water_heater_poll_failed(f_live_house: House) -> House:
    f_live_house.plugs.record_failure('plug-boiler-1f', error_code='device_unavailable', error='timed out')
    return f_live_house


@pytest.fixture
def f_live_house_after_a_shed(f_live_house: House) -> House:
    _seconds(f_live_house.guard, 4, '26.0')
    return f_live_house


@pytest.fixture
def f_live_house_after_two_sheds(f_live_house: House) -> House:
    _seconds(f_live_house.guard, 5, '40.0')
    return f_live_house


@pytest.fixture
def f_live_house_shedding_thirty_amperes(f_live_house: House) -> House:
    _seconds(f_live_house.guard, 5, '30.0')
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
def f_restarted_house_after_a_zone_start(f_restarted_house: House) -> House:
    _seconds(f_restarted_house.guard, 110, '10.0')
    f_restarted_house.book.permit_start(
        KITCHEN_START,
        meter=MeterCondition.FRESH,
        view=GuardView(
            currents={LoadPhase.L1: Decimal('8.0'), LoadPhase.L2: Decimal('10.0'), LoadPhase.L3: Decimal('4.0')},
            loads=(),
        ),
        at=START + 110,
        thresholds=LIVE_CONTROL.thresholds,
    )
    return f_restarted_house


@pytest.fixture
def f_restarted_house_with_a_zone_shed_twenty_minutes_ago() -> House:
    return _house(
        [replace(KITCHEN, live=True, shed=replace(STORED_SHED, at=NOW - timedelta(minutes=20), was_on=None))],
        LIVE_CONTROL,
        frozenset(),
    )


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
def f_overload_after_a_failed_plug_shed(f_unreachable_plug_house: House) -> House:
    _seconds(f_unreachable_plug_house.guard, 5, '26.0')
    return f_unreachable_plug_house


@pytest.fixture
def f_live_floors_house() -> House:
    return _house(_live(KITCHEN, LIVING_ROOM), LIVE_CONTROL, LIVE_ZONES)


@pytest.fixture
def f_live_floors_house_after_a_shed(f_live_floors_house: House) -> House:
    _seconds(f_live_floors_house.guard, 4, '26.0')
    return f_live_floors_house


@pytest.fixture
def f_live_floors_house_after_a_shed_whose_stamp_was_lost() -> House:
    house = _house(_live(KITCHEN, LIVING_ROOM), LIVE_CONTROL, LIVE_ZONES, zone_stamped=False)
    _seconds(house.guard, 4, '26.0')
    return house


@pytest.fixture
def f_mixed_house() -> House:
    return _house([BOILER, KITCHEN, replace(LIVING_ROOM, live=True)], LIVE_CONTROL, LIVE_ZONES)


@pytest.fixture
def f_mixed_house_after_an_overload() -> House:
    house = _house([replace(BOILER, live=True), KITCHEN], LIVE_CONTROL, LIVE_ZONES)
    _seconds(house.guard, 4, '30.0')
    return house


@pytest.fixture
def f_mixed_floors_house(request: pytest.FixtureRequest) -> House:
    return _house(request.param, LIVE_CONTROL, LIVE_ZONES)


@pytest.fixture
def f_house_with_an_unswitchable_shed_plug() -> House:
    return _house(
        [replace(BOILER, live=True, shed=STORED_SHED), replace(BOILER_2F, live=True, shed=STORED_SHED)],
        LIVE_CONTROL,
        frozenset(),
        boiler_on=False,
    )


@pytest.fixture
def f_house_with_an_unreachable_shed_plug() -> House:
    return _house(
        [replace(BOILER, live=True, shed=STORED_SHED)],
        LIVE_CONTROL,
        frozenset(),
        boiler_on=False,
        boiler_error=DeviceUnavailableError('timed out'),
    )


@pytest.fixture
def unswitchable_shed_plug_house() -> House:
    return _house([replace(BOILER_2F, live=True, shed=STORED_SHED)], LIVE_CONTROL, frozenset(), boiler_on=False)


@pytest.fixture
def f_unswitchable_plug_after_a_failed_restore(unswitchable_shed_plug_house: House) -> House:
    _seconds(unswitchable_shed_plug_house.guard, 150, '10.0')
    _plug_read_off(unswitchable_shed_plug_house, 'plug-boiler-2f', 150)
    return unswitchable_shed_plug_house


@pytest.fixture
def f_unswitchable_plug_after_a_failed_release(unswitchable_shed_plug_house: House) -> House:
    _seconds(unswitchable_shed_plug_house.guard, 1, '10.0')
    _load_off(unswitchable_shed_plug_house)
    _seconds(unswitchable_shed_plug_house.guard, 119, '10.0')
    _plug_read_off(unswitchable_shed_plug_house, 'plug-boiler-2f', 120)
    return unswitchable_shed_plug_house


@pytest.fixture
def house_whose_database_fails_once() -> House:
    return _house(_live(BOILER), LIVE_CONTROL, frozenset(), write_errors=(OperationalError('database is locked'),))


@pytest.fixture
def f_shed_the_database_failed_to_store(house_whose_database_fails_once: House) -> House:
    _seconds(house_whose_database_fails_once.guard, 63, '26.0')
    return house_whose_database_fails_once


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
            switcher=LoadSwitcher(plugs=SwitchablePlugProviderStub(plugs={}), zones=ZoneSwitchStub(error=None)),
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


def test_one_reading_above_the_shed_threshold_that_the_meter_does_not_follow_up_sheds_nothing(
    f_live_house: House,
) -> None:
    spike = _seconds(f_live_house.guard, 1, '25.5')
    stalled = _seconds(f_live_house.guard, 4, None)
    resumed = _seconds(f_live_house.guard, 1, '12.0')

    assert ([*spike, *stalled, *resumed], f_live_house.events, f_live_house.boiler.switches) == (
        [NOTHING] * 6,
        [],
        [],
    )


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


def test_load_whose_last_poll_failed_is_passed_over_for_the_next_one(
    f_live_house_whose_water_heater_poll_failed: House,
) -> None:
    outputs = _seconds(f_live_house_whose_water_heater_poll_failed.guard, 4, '26.0')

    assert (
        _shed_at(outputs, 1),
        f_live_house_whose_water_heater_poll_failed.boiler.switches,
        f_live_house_whose_water_heater_poll_failed.zones.switched_off,
    ) == ([(4, ('kitchen',))], [], [('kitchen', 'thermostat-kitchen', _moment(4))])


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
                switched_on=False,
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
                switched_on=True,
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
                amps=Decimal('4.0'),
                load_amps=Decimal('6.0'),
                reason=ShedReason.OVERLOAD,
                cause=RestoreCause.CALM,
                switched_on=False,
                occurred_at=_moment(125),
            ),
        ],
    )


def test_dry_run_restore_journals_the_current_of_the_dry_run_view_it_was_decided_on(
    f_dry_run_house_after_a_shed: House,
) -> None:
    outputs = _seconds(f_dry_run_house_after_a_shed.guard, 121, '19.0')

    assert (_restored_at(outputs, 5), f_dry_run_house_after_a_shed.events[2:]) == (
        [(125, ('boiler-1f',))],
        [
            LoadRestoredDryRun(
                load='boiler-1f',
                name='Бойлер 1 этаж',
                kind=LoadKind.PLUG,
                phase=LoadPhase.L2,
                amps=Decimal('13.0'),
                load_amps=Decimal('6.0'),
                reason=ShedReason.OVERLOAD,
                cause=RestoreCause.CALM,
                switched_on=False,
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
                switched_on=True,
                occurred_at=_moment(121),
            ),
        ],
    )


def test_plug_restore_waiting_for_a_start_permit_looks_its_plug_up_only_once_the_permit_is_due(
    f_restarted_house_after_a_zone_start: House,
) -> None:
    outputs = _seconds(f_restarted_house_after_a_zone_start.guard, 30, '10.0')

    assert (
        _restored_at(outputs, 111),
        f_restarted_house_after_a_zone_start.plug_provider.asked,
        f_restarted_house_after_a_zone_start.book.last_permit_at(),
    ) == ([(140, ('boiler-1f',))], ['plug-boiler-1f'], START + 140)


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
                switched_on=True,
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
                switched_on=False,
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


def test_plug_shed_whose_write_failed_is_taken_as_shed_once_the_plug_reads_off(
    f_overload_after_a_failed_plug_shed: House,
) -> None:
    _plug_read_off(f_overload_after_a_failed_plug_shed, 'plug-boiler-1f', 5)
    outputs = _seconds(f_overload_after_a_failed_plug_shed.guard, 1, '18.0')

    assert (
        outputs,
        f_overload_after_a_failed_plug_shed.loads.writes,
        f_overload_after_a_failed_plug_shed.events[3:],
        _unconfirmed_sheds(f_overload_after_a_failed_plug_shed),
    ) == (
        [ShedPhaseLoadsOutputDTO(shed=('boiler-1f',), restored=())],
        [
            (
                'mark_shed',
                'kitchen',
                LiveShed(at=_moment(5), reason=ShedReason.OVERLOAD, phase=LoadPhase.L2, was_on=None),
            ),
            (
                'mark_shed',
                'boiler-1f',
                LiveShed(at=_moment(4), reason=ShedReason.OVERLOAD, phase=LoadPhase.L2, was_on=True),
            ),
        ],
        [
            LoadShed(
                load='boiler-1f',
                name='Бойлер 1 этаж',
                kind=LoadKind.PLUG,
                phase=LoadPhase.L2,
                amps=Decimal('26.0'),
                load_amps=Decimal('6.0'),
                reason=ShedReason.OVERLOAD,
                occurred_at=_moment(6),
            ),
        ],
        {},
    )


def test_plug_shed_whose_write_failed_is_dropped_once_the_plug_still_reads_on(
    f_overload_after_a_failed_plug_shed: House,
) -> None:
    f_overload_after_a_failed_plug_shed.plugs.record_state('plug-boiler-1f', _plug(on=True, second=5))
    outputs = _seconds(f_overload_after_a_failed_plug_shed.guard, 1, '18.0')

    assert (
        outputs,
        f_overload_after_a_failed_plug_shed.loads.writes[1:],
        f_overload_after_a_failed_plug_shed.events[3:],
        _unconfirmed_sheds(f_overload_after_a_failed_plug_shed),
    ) == ([NOTHING], [], [], {})


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
                amps=Decimal('2.0'),
                load_amps=Decimal('8.0'),
                reason=ShedReason.METER_LOST,
                cause=RestoreCause.CALM,
                switched_on=False,
                occurred_at=_moment(201),
            ),
        ],
    )


def test_live_load_is_shed_on_the_measured_current_whatever_the_dry_run_sheds(f_mixed_house: House) -> None:
    outputs = _seconds(f_mixed_house.guard, 6, '30.0')

    assert (
        _shed_at(outputs, 1),
        f_mixed_house.zones.switched_off,
        f_mixed_house.boiler.switches,
        f_mixed_house.events,
    ) == (
        [(4, ('living-room', 'boiler-1f'))],
        [('living-room', 'thermostat-living-room', _moment(4))],
        [],
        [
            _overload(4, '30.0'),
            LoadShed(
                load='living-room',
                name='Гостиная',
                kind=LoadKind.ZONE,
                phase=LoadPhase.L2,
                amps=Decimal('30.0'),
                load_amps=Decimal('8.0'),
                reason=ShedReason.OVERLOAD,
                occurred_at=_moment(4),
            ),
            LoadShedDryRun(
                load='boiler-1f',
                name='Бойлер 1 этаж',
                kind=LoadKind.PLUG,
                phase=LoadPhase.L2,
                amps=Decimal('30.0'),
                load_amps=Decimal('6.0'),
                reason=ShedReason.OVERLOAD,
                occurred_at=_moment(4),
            ),
        ],
    )


def test_live_plug_goes_back_on_only_when_the_measured_phase_has_room(
    f_mixed_house_after_an_overload: House,
) -> None:
    crowded = _seconds(f_mixed_house_after_an_overload.guard, 130, '16.0')
    switched_while_crowded = list(f_mixed_house_after_an_overload.boiler.switches)
    permit_while_crowded = f_mixed_house_after_an_overload.book.last_permit_at()
    roomy = _seconds(f_mixed_house_after_an_overload.guard, 1, '14.0')

    assert (
        _restored_at(crowded, 5),
        switched_while_crowded,
        permit_while_crowded,
        _restored_at(roomy, 135),
        f_mixed_house_after_an_overload.boiler.switches,
        f_mixed_house_after_an_overload.book.last_permit_at(),
    ) == (
        [(125, ('kitchen',))],
        [False],
        None,
        [(135, ('boiler-1f',))],
        [False, True],
        START + 135,
    )


@pytest.mark.parametrize(
    ('f_mixed_floors_house', 'shed', 'switched_off'),
    [
        (
            [replace(KITCHEN, live=True), LIVING_ROOM],
            [(61, ('kitchen',))],
            [('kitchen', 'thermostat-kitchen', _moment(61))],
        ),
        ([KITCHEN, replace(LIVING_ROOM, live=True)], [], []),
    ],
    ids=['live_floor_beside_a_more_important_one', 'most_important_floor_beside_one_it_cannot_switch'],
    indirect=['f_mixed_floors_house'],
)
def test_lost_meter_sheds_a_live_floor_only_beside_a_more_important_drawing_floor(
    f_mixed_floors_house: House,
    shed: list[tuple[int, tuple[str, ...]]],
    switched_off: list[tuple[str, str, datetime]],
) -> None:
    outputs = _seconds(f_mixed_floors_house.guard, 62, None)

    assert (_shed_at(outputs, 1), f_mixed_floors_house.zones.switched_off) == (shed, switched_off)


def test_live_shed_relieves_its_phase_until_the_meter_reads_the_phase_once_it_settled(
    f_live_house_shedding_thirty_amperes: House,
) -> None:
    outputs = _seconds(f_live_house_shedding_thirty_amperes.guard, 9, None)

    assert (_shed_at(outputs, 6), f_live_house_shedding_thirty_amperes.zones.switched_off) == (
        [],
        [('kitchen', 'thermostat-kitchen', _moment(5))],
    )


def test_restore_that_fails_is_journaled_and_the_next_shed_goes_back_a_minute_later(
    f_house_with_an_unswitchable_shed_plug: House,
) -> None:
    outputs = _polled_seconds(f_house_with_an_unswitchable_shed_plug, 181, '10.0')

    assert (
        _restored_at(outputs, 1),
        f_house_with_an_unswitchable_shed_plug.boiler.switches,
        f_house_with_an_unswitchable_shed_plug.book.last_permit_at(),
        f_house_with_an_unswitchable_shed_plug.events,
    ) == (
        [(181, ('boiler-1f',))],
        [True],
        START + 181,
        [
            _unswitchable_restore(121),
            LoadRestored(
                load='boiler-1f',
                name='Бойлер 1 этаж',
                kind=LoadKind.PLUG,
                phase=LoadPhase.L2,
                amps=Decimal('10.0'),
                load_amps=Decimal('6.0'),
                reason=ShedReason.OVERLOAD,
                cause=RestoreCause.CALM,
                switched_on=True,
                occurred_at=_moment(181),
            ),
        ],
    )


def test_restore_of_an_unreachable_plug_is_tried_once_until_the_plug_is_read_again(
    f_house_with_an_unreachable_shed_plug: House,
) -> None:
    _seconds(f_house_with_an_unreachable_shed_plug.guard, 300, '10.0')

    assert (
        f_house_with_an_unreachable_shed_plug.events,
        f_house_with_an_unreachable_shed_plug.book.last_permit_at(),
        _failed_commands(f_house_with_an_unreachable_shed_plug),
    ) == (
        [
            LoadCommandFailed(
                load='boiler-1f',
                name='Бойлер 1 этаж',
                command=LoadCommand.RESTORE,
                error='timed out',
                error_code='device_unavailable',
                occurred_at=_moment(121),
            ),
        ],
        START + 121,
        {'boiler-1f': START + 121},
    )


def test_restore_that_keeps_failing_is_tried_after_each_reading_and_journaled_once_a_shed(
    f_unswitchable_plug_after_a_failed_restore: House,
) -> None:
    _seconds(f_unswitchable_plug_after_a_failed_restore.guard, 40, '10.0')

    assert (
        f_unswitchable_plug_after_a_failed_restore.events,
        _failed_commands(f_unswitchable_plug_after_a_failed_restore),
        f_unswitchable_plug_after_a_failed_restore.book.last_permit_at(),
    ) == ([_unswitchable_restore(121)], {'boiler-2f': START + 181}, None)


def test_release_that_fails_is_tried_again_only_once_the_plug_is_read_again(
    f_unswitchable_plug_after_a_failed_release: House,
) -> None:
    _seconds(f_unswitchable_plug_after_a_failed_release.guard, 10, '10.0')

    assert (
        f_unswitchable_plug_after_a_failed_release.events,
        _failed_commands(f_unswitchable_plug_after_a_failed_release),
    ) == ([_unswitchable_restore(61)], {'boiler-2f': START + 121})


def test_released_zone_its_cycle_still_writes_returns_to_its_schedule_at_once(
    f_live_floors_house_after_a_shed: House,
) -> None:
    _zone_load_off(f_live_floors_house_after_a_shed)
    outputs = _seconds(f_live_floors_house_after_a_shed.guard, 57, '15.0')

    assert (
        _restored_at(outputs, 5),
        f_live_floors_house_after_a_shed.zones.switched_on,
        f_live_floors_house_after_a_shed.book.held_zones(),
        f_live_floors_house_after_a_shed.events[2:],
    ) == (
        [(61, ('kitchen',))],
        [],
        frozenset(),
        [
            LoadRestored(
                load='kitchen',
                name='Кухня',
                kind=LoadKind.ZONE,
                phase=LoadPhase.L2,
                amps=Decimal('15.0'),
                load_amps=Decimal('8.0'),
                reason=ShedReason.OVERLOAD,
                cause=RestoreCause.RELEASED,
                switched_on=False,
                occurred_at=_moment(61),
            ),
        ],
    )


def test_released_zone_no_longer_written_live_gets_its_thermostat_back_on_at_its_next_allowed_write(
    f_live_floors_house_after_a_shed: House,
) -> None:
    _zone_live_control_off(f_live_floors_house_after_a_shed)
    early = _seconds(f_live_floors_house_after_a_shed.guard, 57, '15.0')
    held = f_live_floors_house_after_a_shed.book.held_zones()
    _pause(f_live_floors_house_after_a_shed.guard, 1740)
    late = _seconds(f_live_floors_house_after_a_shed.guard, 3, '15.0')

    assert (
        _restored_at(early, 5),
        held,
        _restored_at(late, 1802),
        f_live_floors_house_after_a_shed.zones.switched_on,
        f_live_floors_house_after_a_shed.events[2:],
    ) == (
        [],
        frozenset({'kitchen'}),
        [(1804, ('kitchen',))],
        [('kitchen', 'thermostat-kitchen', _moment(1804))],
        [
            PhaseCalmed(phase=LoadPhase.L2, amps=Decimal('15.0'), occurred_at=_moment(1802)),
            LoadRestored(
                load='kitchen',
                name='Кухня',
                kind=LoadKind.ZONE,
                phase=LoadPhase.L2,
                amps=Decimal('15.0'),
                load_amps=Decimal('8.0'),
                reason=ShedReason.OVERLOAD,
                cause=RestoreCause.RELEASED,
                switched_on=True,
                occurred_at=_moment(1804),
            ),
        ],
    )


def test_zone_shed_resumed_without_live_control_gets_its_thermostat_back_on_thirty_minutes_after_the_shed(
    f_restarted_house_with_a_zone_shed_twenty_minutes_ago: House,
) -> None:
    early = _seconds(f_restarted_house_with_a_zone_shed_twenty_minutes_ago.guard, 1, '10.0')
    _pause(f_restarted_house_with_a_zone_shed_twenty_minutes_ago.guard, 597)
    late = _seconds(f_restarted_house_with_a_zone_shed_twenty_minutes_ago.guard, 3, '10.0')

    assert (
        _restored_at(early, 1),
        _restored_at(late, 599),
        f_restarted_house_with_a_zone_shed_twenty_minutes_ago.zones.switched_on,
    ) == ([], [(600, ('kitchen',))], [('kitchen', 'thermostat-kitchen', _moment(600))])


def test_zone_shed_whose_write_stamp_was_lost_stays_held_until_thirty_minutes_after_the_shed(
    f_live_floors_house_after_a_shed_whose_stamp_was_lost: House,
) -> None:
    calm = _seconds(f_live_floors_house_after_a_shed_whose_stamp_was_lost.guard, 300, '10.0')
    held = f_live_floors_house_after_a_shed_whose_stamp_was_lost.book.held_zones()
    _pause(f_live_floors_house_after_a_shed_whose_stamp_was_lost.guard, 1495)
    _thermostat_read_off(f_live_floors_house_after_a_shed_whose_stamp_was_lost, 'thermostat-kitchen', 1799)
    late = _seconds(f_live_floors_house_after_a_shed_whose_stamp_was_lost.guard, 6, '10.0')

    assert (
        _restored_at(calm, 5),
        held,
        _restored_at(late, 1800),
        f_live_floors_house_after_a_shed_whose_stamp_was_lost.book.held_zones(),
    ) == ([], frozenset({'kitchen'}), [(1804, ('kitchen',))], frozenset())


def test_live_shed_the_database_fails_to_store_is_kept_and_stored_a_minute_later(
    f_shed_the_database_failed_to_store: House,
) -> None:
    unstored = list(f_shed_the_database_failed_to_store.loads.writes)
    outputs = _seconds(f_shed_the_database_failed_to_store.guard, 1, '26.0')

    assert (
        unstored,
        outputs,
        f_shed_the_database_failed_to_store.boiler.switches,
        f_shed_the_database_failed_to_store.loads.writes,
    ) == (
        [],
        [NOTHING],
        [False],
        [
            (
                'mark_shed',
                'boiler-1f',
                LiveShed(at=_moment(4), reason=ShedReason.OVERLOAD, phase=LoadPhase.L2, was_on=True),
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
