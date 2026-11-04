import threading
from dataclasses import replace
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.db import OperationalError
from structlog.testing import capture_logs

from apps.climate.domain.backup_heat import BackupHeat
from apps.climate.domain.control import ClimateGates
from apps.climate.domain.drive import StartAnswer
from apps.climate.domain.events import SourceCommandFailed, SourceCommandIssued
from apps.climate.domain.fast_source import SourceStart
from apps.climate.domain.heat_sources import HeatSourceKind, SourceSettings
from apps.climate.domain.modes import ZoneCycleOutcome
from apps.climate.domain.room_climate import RoomClimateSettings
from apps.climate.domain.room_intent import GuestsRecord
from apps.climate.domain.sensors import ClimateSensors
from apps.climate.domain.zone import AirTargets, ComfortWindow
from apps.climate.services.control_book import ControlBook
from apps.climate.services.room_intents import RoomIntentReader
from apps.climate.services.write_gate import KeyedWriteGate
from apps.climate.stubs import (
    ClimateGatesStoreStub,
    ClimateSensorsStoreStub,
    FireplaceStoreStub,
    GuestsStoreStub,
    HeatSourceStoreStub,
    RoomClimateStoreStub,
    SourceLoadGateStub,
)
from apps.climate.usecases.run_source_cycle import (
    RunSourceCycleInputDTO,
    RunSourceCycleOutputDTO,
    RunSourceCycleUseCase,
)
from apps.core.domain.events import DomainEvent, EventDispatcher
from apps.core.stubs import MonotonicClockStub, TransactionStub
from apps.devices.domain.errors import DeviceUnavailableError
from apps.devices.domain.readings import ClimateMode, ClimateUnitState
from apps.devices.services.state_store import ClimateUnitStateStore, TemperatureStateStore
from apps.devices.stubs import ControllableClimateUnitProviderStub, ControllableClimateUnitStub
from apps.power.domain.events import StartWouldWaitDryRun

MOSCOW = ZoneInfo('Europe/Moscow')
COMFORT_AT = datetime(2026, 11, 4, 13, 30, tzinfo=MOSCOW)
SETBACK_AT = datetime(2026, 11, 4, 12, 0, tzinfo=MOSCOW)
MONOTONIC = 1000.0
INTERVAL = timedelta(minutes=5)
NO_BACKUP = BackupHeat(zone_slug=None, below=Decimal('19.0'), hysteresis=Decimal('0.30'), live=False)
OFFICE = RoomClimateSettings(
    slug='office',
    name='Кабинет',
    air_sensor_slug=None,
    targets=AirTargets(comfort=Decimal('21.5'), night=Decimal('19.0')),
    windows=tuple(ComfortWindow(weekday=weekday, start=time(13, 0), end=time(23, 45)) for weekday in range(7)),
    backup=NO_BACKUP,
    guest_room=False,
    override=None,
    adjustable=True,
)
BEDROOM = replace(
    OFFICE,
    slug='bedroom',
    name='Спальня',
    targets=AirTargets(comfort=Decimal('20.5'), night=Decimal('19.5')),
    windows=(),
)
WATCHED_CONVECTOR = SourceSettings(
    slug='convector-office',
    name='Конвектор: кабинет',
    room_slug='office',
    room_name='Кабинет',
    kind=HeatSourceKind.HEATER,
    device_slug='heater-office',
    priority=1,
    live=False,
    setpoint_bias=Decimal('0.0'),
    max_setpoint=Decimal('24.0'),
    min_outdoor=None,
    write_interval=INTERVAL,
    rated_watts=2000,
    manual_until=None,
    last_command_at=None,
    last_dry_run_at=None,
    commanded_power=None,
    commanded_setpoint=None,
    load='convector-office',
    load_live=False,
    load_management_live=False,
)
LIVE_CONVECTOR = replace(WATCHED_CONVECTOR, live=True, load_live=True, load_management_live=True)
LIVE_BEDROOM_HEATER = replace(
    LIVE_CONVECTOR,
    slug='heater-bedroom',
    name='Обогреватель: спальня',
    room_slug='bedroom',
    room_name='Спальня',
    device_slug='heater-bedroom',
    load='heater-bedroom',
)
AT_THE_APPS_SETPOINT = ClimateUnitState(
    mode=ClimateMode.HEAT,
    target=Decimal(35),
    room_temperature=Decimal(21),
    read_at=SETBACK_AT,
    level='High',
)
AT_NINETEEN = replace(AT_THE_APPS_SETPOINT, target=Decimal(19), room_temperature=Decimal(19))
OFF_AT_NINETEEN = replace(AT_NINETEEN, mode=ClimateMode.OFF)
GRANTED = {
    'convector-office': StartAnswer(granted=True, note=None),
    'heater-bedroom': StartAnswer(granted=True, note=None),
}


def _issued(setpoint: Decimal, at: datetime, *, dry_run: bool) -> SourceCommandIssued:
    return SourceCommandIssued(
        source='convector-office',
        name='Конвектор: кабинет',
        power=None,
        setpoint=setpoint,
        dry_run=dry_run,
        occurred_at=at,
    )


def _cycle(at: datetime) -> RunSourceCycleInputDTO:
    return RunSourceCycleInputDTO(now=at, monotonic=MONOTONIC, clock_trusted=True)


@pytest.fixture
def f_events() -> list[DomainEvent]:
    return []


@pytest.fixture
def dispatcher(f_events: list[DomainEvent]) -> EventDispatcher:
    event_dispatcher = EventDispatcher()
    for event_type in (SourceCommandIssued, SourceCommandFailed, StartWouldWaitDryRun):
        event_dispatcher.register(event_type, f_events.append)
    return event_dispatcher


@pytest.fixture
def clock() -> MonotonicClockStub:
    return MonotonicClockStub(now=MONOTONIC)


@pytest.fixture
def f_unit(request: pytest.FixtureRequest) -> ControllableClimateUnitStub:
    errors = getattr(request, 'param', ())
    return ControllableClimateUnitStub(state=None, command_errors=errors)


@pytest.fixture
def f_bedroom_unit() -> ControllableClimateUnitStub:
    return ControllableClimateUnitStub(state=None)


@pytest.fixture
def units(clock: MonotonicClockStub, request: pytest.FixtureRequest) -> ClimateUnitStateStore:
    store = ClimateUnitStateStore(unavailable_after=timedelta(minutes=10), clock=clock)
    store.record_state('heater-office', getattr(request, 'param', AT_THE_APPS_SETPOINT))
    store.record_state('heater-bedroom', AT_NINETEEN)
    return store


@pytest.fixture
def f_sources(request: pytest.FixtureRequest) -> HeatSourceStoreStub:
    sources, errors = getattr(request, 'param', ([WATCHED_CONVECTOR], {}))
    return HeatSourceStoreStub(sources=sources, errors=errors)


@pytest.fixture
def f_gate(request: pytest.FixtureRequest) -> SourceLoadGateStub:
    held, gated, answers = getattr(request, 'param', (frozenset(), frozenset(GRANTED), GRANTED))
    return SourceLoadGateStub(held=held, gated=gated, answers=answers)


@pytest.fixture
def book() -> ControlBook:
    return ControlBook()


@pytest.fixture
def f_use_case(
    f_sources: HeatSourceStoreStub,
    units: ClimateUnitStateStore,
    f_unit: ControllableClimateUnitStub,
    f_bedroom_unit: ControllableClimateUnitStub,
    f_gate: SourceLoadGateStub,
    book: ControlBook,
    clock: MonotonicClockStub,
    dispatcher: EventDispatcher,
) -> RunSourceCycleUseCase:
    return RunSourceCycleUseCase(
        sources=f_sources,
        intents=RoomIntentReader(
            rooms=RoomClimateStoreStub(rooms=[OFFICE, BEDROOM]),
            guests=GuestsStoreStub(record=GuestsRecord(until=None, end_hour=14)),
            fireplaces=FireplaceStoreStub(fireplaces=[]),
            gates=ClimateGatesStoreStub(gates=ClimateGates(manual_hold_live=False)),
            book=book,
        ),
        climate_settings=ClimateSensorsStoreStub(sensors=ClimateSensors(outdoor_slug=None)),
        units=units,
        temperatures=TemperatureStateStore(unavailable_after=timedelta(minutes=10), clock=clock),
        provider=ControllableClimateUnitProviderStub(units={'heater-office': f_unit, 'heater-bedroom': f_bedroom_unit}),
        book=book,
        gate=f_gate,
        writes=KeyedWriteGate(clock=clock, key_lock=threading.Lock),
        transaction=TransactionStub(meanwhile=None),
        dry_run=False,
        dispatcher=dispatcher,
    )


@pytest.fixture
def f_use_case_that_journaled(f_use_case: RunSourceCycleUseCase) -> RunSourceCycleUseCase:
    f_use_case.execute(_cycle(COMFORT_AT))
    return f_use_case


@pytest.fixture
def f_use_case_that_journaled_the_setback(f_use_case: RunSourceCycleUseCase) -> RunSourceCycleUseCase:
    f_use_case.execute(_cycle(SETBACK_AT))
    return f_use_case


def test_watched_source_journals_what_it_would_write_after_stamping_it(
    f_use_case: RunSourceCycleUseCase,
    f_sources: HeatSourceStoreStub,
    f_unit: ControllableClimateUnitStub,
    f_events: list[DomainEvent],
) -> None:
    output = f_use_case.execute(_cycle(COMFORT_AT))

    assert (output, f_sources.stamps, f_unit.commands, f_events) == (
        RunSourceCycleOutputDTO(outcomes={'convector-office': ZoneCycleOutcome.DRY_RUN}),
        [('last_dry_run_at', 'convector-office', COMFORT_AT)],
        [],
        [_issued(Decimal(22), COMFORT_AT, dry_run=True)],
    )


def test_watched_source_journals_a_would_be_target_once(
    f_use_case_that_journaled: RunSourceCycleUseCase,
    f_events: list[DomainEvent],
) -> None:
    output = f_use_case_that_journaled.execute(_cycle(COMFORT_AT + timedelta(minutes=10)))

    assert (output, f_events) == (
        RunSourceCycleOutputDTO(outcomes={'convector-office': ZoneCycleOutcome.UNCHANGED}),
        [_issued(Decimal(22), COMFORT_AT, dry_run=True)],
    )


def test_watched_source_journals_again_once_its_would_be_target_changes(
    f_use_case_that_journaled_the_setback: RunSourceCycleUseCase,
    f_events: list[DomainEvent],
) -> None:
    f_use_case_that_journaled_the_setback.execute(_cycle(COMFORT_AT))

    assert f_events == [_issued(Decimal(19), SETBACK_AT, dry_run=True), _issued(Decimal(22), COMFORT_AT, dry_run=True)]


@pytest.mark.parametrize(
    'f_sources',
    [([replace(WATCHED_CONVECTOR, last_dry_run_at=COMFORT_AT - timedelta(minutes=2))], {})],
    indirect=True,
)
def test_watched_source_journals_no_sooner_than_its_interval_after_its_last_entry(
    f_use_case: RunSourceCycleUseCase,
    f_events: list[DomainEvent],
) -> None:
    output = f_use_case.execute(_cycle(COMFORT_AT))

    assert (output, f_events) == (
        RunSourceCycleOutputDTO(outcomes={'convector-office': ZoneCycleOutcome.THROTTLED}),
        [],
    )


@pytest.mark.parametrize(
    ('f_sources', 'units'),
    [(([LIVE_CONVECTOR], {}), AT_NINETEEN)],
    indirect=True,
)
def test_live_source_that_is_on_gets_its_setpoint_stamped_first_without_a_start_permit(
    f_use_case: RunSourceCycleUseCase,
    f_sources: HeatSourceStoreStub,
    f_unit: ControllableClimateUnitStub,
    f_gate: SourceLoadGateStub,
    f_events: list[DomainEvent],
) -> None:
    output = f_use_case.execute(_cycle(COMFORT_AT))

    assert (output, f_sources.stamps, f_sources.commanded, f_unit.commands, f_gate.asked, f_gate.started, f_events) == (
        RunSourceCycleOutputDTO(outcomes={'convector-office': ZoneCycleOutcome.APPLIED}),
        [('last_command_at', 'convector-office', COMFORT_AT)],
        [('convector-office', True, Decimal(22))],
        [('setpoint', Decimal(22))],
        [()],
        [],
        [_issued(Decimal(22), COMFORT_AT, dry_run=False)],
    )


@pytest.mark.parametrize(
    ('f_sources', 'units'),
    [(([LIVE_CONVECTOR], {}), OFF_AT_NINETEEN)],
    indirect=True,
)
def test_live_source_switched_on_takes_a_start_permit_and_its_setpoint_before_its_power(
    f_use_case: RunSourceCycleUseCase,
    f_sources: HeatSourceStoreStub,
    f_unit: ControllableClimateUnitStub,
    f_gate: SourceLoadGateStub,
    f_events: list[DomainEvent],
) -> None:
    output = f_use_case.execute(_cycle(COMFORT_AT))

    assert (output, f_sources.commanded, f_unit.commands, f_gate.asked, f_gate.started, f_events) == (
        RunSourceCycleOutputDTO(outcomes={'convector-office': ZoneCycleOutcome.APPLIED}),
        [('convector-office', True, Decimal(22))],
        [('setpoint', Decimal(22)), ('power', True)],
        [(SourceStart(source='convector-office', writes_live=True),)],
        ['convector-office'],
        [
            SourceCommandIssued(
                source='convector-office',
                name='Конвектор: кабинет',
                power=True,
                setpoint=Decimal(22),
                dry_run=False,
                occurred_at=COMFORT_AT,
            ),
        ],
    )


@pytest.mark.parametrize(
    ('f_sources', 'units', 'f_unit'),
    [(([LIVE_CONVECTOR], {}), OFF_AT_NINETEEN, (DeviceUnavailableError('timeout'),))],
    indirect=True,
)
def test_source_whose_setpoint_is_refused_is_not_switched_on(
    f_use_case: RunSourceCycleUseCase,
    f_unit: ControllableClimateUnitStub,
    f_events: list[DomainEvent],
) -> None:
    output = f_use_case.execute(_cycle(COMFORT_AT))

    assert (output, f_unit.commands, f_events) == (
        RunSourceCycleOutputDTO(outcomes={'convector-office': ZoneCycleOutcome.FAILED}),
        [],
        [
            SourceCommandFailed(
                source='convector-office',
                name='Конвектор: кабинет',
                power=True,
                setpoint=Decimal(22),
                error='timeout',
                error_code='device_unavailable',
                occurred_at=COMFORT_AT,
            ),
        ],
    )


@pytest.mark.parametrize(
    ('f_sources', 'units', 'f_gate'),
    [(([LIVE_CONVECTOR], {}), OFF_AT_NINETEEN, (frozenset(), frozenset(), GRANTED))],
    indirect=True,
)
def test_live_source_waits_until_load_management_serves_its_load_live(
    f_use_case: RunSourceCycleUseCase,
    f_sources: HeatSourceStoreStub,
    f_unit: ControllableClimateUnitStub,
    f_gate: SourceLoadGateStub,
) -> None:
    output = f_use_case.execute(_cycle(COMFORT_AT))

    assert (output, f_sources.stamps, f_unit.commands, f_gate.asked) == (
        RunSourceCycleOutputDTO(outcomes={'convector-office': ZoneCycleOutcome.WAITING}),
        [],
        [],
        [()],
    )


@pytest.mark.parametrize(
    ('f_sources', 'units'),
    [(([replace(LIVE_CONVECTOR, last_command_at=COMFORT_AT - timedelta(minutes=4))], {}), AT_NINETEEN)],
    indirect=True,
)
def test_live_source_is_written_no_sooner_than_its_interval_after_its_last_write(
    f_use_case: RunSourceCycleUseCase,
    f_unit: ControllableClimateUnitStub,
    f_gate: SourceLoadGateStub,
) -> None:
    output = f_use_case.execute(_cycle(COMFORT_AT))

    assert (output, f_unit.commands, f_gate.asked) == (
        RunSourceCycleOutputDTO(outcomes={'convector-office': ZoneCycleOutcome.THROTTLED}),
        [],
        [()],
    )


@pytest.mark.parametrize(
    ('f_sources', 'units', 'f_gate'),
    [
        (
            ([LIVE_CONVECTOR], {}),
            OFF_AT_NINETEEN,
            (frozenset(), frozenset(GRANTED), {'convector-office': StartAnswer(granted=False, note=None)}),
        ),
    ],
    indirect=True,
)
def test_live_source_whose_start_is_refused_waits_and_writes_nothing(
    f_use_case: RunSourceCycleUseCase,
    f_sources: HeatSourceStoreStub,
    f_unit: ControllableClimateUnitStub,
    f_gate: SourceLoadGateStub,
) -> None:
    output = f_use_case.execute(_cycle(COMFORT_AT))

    assert (output, f_sources.stamps, f_unit.commands, f_gate.started) == (
        RunSourceCycleOutputDTO(outcomes={'convector-office': ZoneCycleOutcome.WAITING}),
        [],
        [],
        [],
    )


@pytest.mark.parametrize(
    ('f_sources', 'units', 'f_gate'),
    [(([LIVE_CONVECTOR], {}), AT_NINETEEN, (frozenset({'convector-office'}), frozenset(GRANTED), {}))],
    indirect=True,
)
def test_source_load_management_holds_shed_gets_no_write(
    f_use_case: RunSourceCycleUseCase,
    f_unit: ControllableClimateUnitStub,
    f_gate: SourceLoadGateStub,
) -> None:
    output = f_use_case.execute(_cycle(COMFORT_AT))

    assert (output, f_unit.commands, f_gate.asked) == (
        RunSourceCycleOutputDTO(outcomes={'convector-office': ZoneCycleOutcome.SHED}),
        [],
        [()],
    )


@pytest.mark.parametrize(
    ('f_sources', 'units'),
    [(([replace(LIVE_CONVECTOR, manual_until=COMFORT_AT + timedelta(hours=1))], {}), AT_NINETEEN)],
    indirect=True,
)
def test_source_changed_by_hand_is_left_alone(
    f_use_case: RunSourceCycleUseCase,
    f_unit: ControllableClimateUnitStub,
) -> None:
    output = f_use_case.execute(_cycle(COMFORT_AT))

    assert (output, f_unit.commands) == (
        RunSourceCycleOutputDTO(outcomes={'convector-office': ZoneCycleOutcome.HANDS_OFF}),
        [],
    )


@pytest.mark.parametrize(
    ('f_sources', 'units', 'f_unit'),
    [(([LIVE_CONVECTOR], {}), AT_NINETEEN, (DeviceUnavailableError('timeout'),))],
    indirect=True,
)
def test_failed_command_is_journaled_and_keeps_its_stamp(
    f_use_case: RunSourceCycleUseCase,
    f_sources: HeatSourceStoreStub,
    f_events: list[DomainEvent],
) -> None:
    output = f_use_case.execute(_cycle(COMFORT_AT))

    assert (output, f_sources.stamps, f_events) == (
        RunSourceCycleOutputDTO(outcomes={'convector-office': ZoneCycleOutcome.FAILED}),
        [('last_command_at', 'convector-office', COMFORT_AT)],
        [
            SourceCommandFailed(
                source='convector-office',
                name='Конвектор: кабинет',
                power=None,
                setpoint=Decimal(22),
                error='timeout',
                error_code='device_unavailable',
                occurred_at=COMFORT_AT,
            ),
        ],
    )


@pytest.mark.parametrize(
    'f_sources',
    [([replace(WATCHED_CONVECTOR, room_slug='attic', room_name='Чердак')], {})],
    indirect=True,
)
def test_source_in_a_room_without_its_climate_is_skipped_and_logged(f_use_case: RunSourceCycleUseCase) -> None:
    with capture_logs() as logs:
        output = f_use_case.execute(_cycle(COMFORT_AT))

    assert (output, [log['event'] for log in logs if log['event'] == 'source_room_without_climate']) == (
        RunSourceCycleOutputDTO(outcomes={}),
        ['source_room_without_climate'],
    )


@pytest.mark.parametrize(
    ('f_sources', 'units'),
    [
        (
            (
                [LIVE_CONVECTOR, LIVE_BEDROOM_HEATER],
                {('last_command_at', 'convector-office'): OperationalError('database is locked')},
            ),
            AT_NINETEEN,
        ),
    ],
    indirect=True,
)
def test_source_whose_stamp_the_database_refuses_fails_while_the_others_run(
    f_use_case: RunSourceCycleUseCase,
    f_unit: ControllableClimateUnitStub,
    f_bedroom_unit: ControllableClimateUnitStub,
) -> None:
    output = f_use_case.execute(_cycle(COMFORT_AT))

    assert (output, f_unit.commands, f_bedroom_unit.commands) == (
        RunSourceCycleOutputDTO(
            outcomes={'convector-office': ZoneCycleOutcome.FAILED, 'heater-bedroom': ZoneCycleOutcome.APPLIED},
        ),
        [],
        [('setpoint', Decimal(20))],
    )


@pytest.mark.parametrize('f_sources', [([], {})], indirect=True)
def test_house_without_a_commanded_source_runs_nothing(f_use_case: RunSourceCycleUseCase) -> None:
    assert f_use_case.execute(_cycle(COMFORT_AT)) == RunSourceCycleOutputDTO(outcomes={})
