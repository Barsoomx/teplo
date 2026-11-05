from dataclasses import replace
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.climate.domain.backup_heat import BackupHeat
from apps.climate.domain.drive import PendingKind, PendingWrite
from apps.climate.domain.heat_sources import HeatSourceKind, SourceSettings
from apps.climate.domain.room_climate import RoomClimateSettings
from apps.climate.domain.room_intent import RoomIntents, resolve_room_intents
from apps.climate.domain.room_status import SourceStatus
from apps.climate.domain.sensors import ClimateSensors
from apps.climate.domain.zone import AirTargets, ComfortWindow
from apps.climate.services.control_book import ControlBook
from apps.climate.services.source_status import SourceStatusReader
from apps.climate.stubs import ClimateSensorsStoreStub, HeatSourceStoreStub, SourceLoadGateStub
from apps.core.stubs import MonotonicClockStub
from apps.devices.domain.readings import ClimateMode, ClimateUnitState, TemperatureReading
from apps.devices.services.state_store import ClimateUnitStateStore, TemperatureStateStore

MOSCOW = ZoneInfo('Europe/Moscow')
NOW = datetime(2026, 11, 4, 16, 40, tzinfo=MOSCOW)
INTERVAL = timedelta(minutes=5)
OFFICE = RoomClimateSettings(
    slug='office',
    name='Кабинет',
    air_sensor_slug=None,
    targets=AirTargets(comfort=Decimal('21.5'), night=Decimal('19.0')),
    windows=tuple(ComfortWindow(weekday=weekday, start=time(13, 0), end=time(23, 45)) for weekday in range(7)),
    backup=BackupHeat(zone_slug=None, below=Decimal('19.0'), hysteresis=Decimal('0.30'), live=False),
    guest_room=False,
    override=None,
    adjustable=True,
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
LIVE_CONVECTOR = replace(
    WATCHED_CONVECTOR,
    live=True,
    load_live=True,
    load_management_live=True,
    last_command_at=NOW - timedelta(minutes=2),
)
AT_THE_APPS_SETPOINT = ClimateUnitState(
    mode=ClimateMode.HEAT,
    target=Decimal(35),
    room_temperature=Decimal(21),
    read_at=NOW,
    level='High',
)
BEDROOM = replace(
    OFFICE,
    slug='bedroom',
    name='Спальня',
    targets=AirTargets(comfort=Decimal('20.5'), night=Decimal('19.5')),
    windows=(),
)
AIR_CONDITIONER = replace(
    WATCHED_CONVECTOR,
    slug='ac-bedroom',
    name='Кондиционер: спальня',
    room_slug='bedroom',
    room_name='Спальня',
    kind=HeatSourceKind.AIR_CONDITIONER,
    device_slug='ac-bedroom',
    min_outdoor=Decimal('-15.0'),
    load='ac-bedroom',
)
AC_HEATING = ClimateUnitState(
    mode=ClimateMode.HEAT,
    target=Decimal('19.5'),
    room_temperature=Decimal(19),
    read_at=NOW,
)


@pytest.fixture
def intents() -> RoomIntents:
    return resolve_room_intents([OFFICE], now=NOW, clock_trusted=True, guests_until=None)


@pytest.fixture
def f_bedroom_intents() -> RoomIntents:
    return resolve_room_intents([BEDROOM], now=NOW, clock_trusted=True, guests_until=None)


@pytest.fixture
def clock() -> MonotonicClockStub:
    return MonotonicClockStub(now=1000.0)


@pytest.fixture
def f_book(request: pytest.FixtureRequest) -> ControlBook:
    book = ControlBook()
    book.remember_lockout('ac-bedroom', getattr(request, 'param', False))
    return book


@pytest.fixture
def units(clock: MonotonicClockStub) -> ClimateUnitStateStore:
    store = ClimateUnitStateStore(unavailable_after=timedelta(minutes=10), clock=clock)
    store.record_state('heater-office', AT_THE_APPS_SETPOINT)
    return store


@pytest.fixture
def f_reader(
    request: pytest.FixtureRequest, units: ClimateUnitStateStore, clock: MonotonicClockStub
) -> SourceStatusReader:
    source, held = getattr(request, 'param', (WATCHED_CONVECTOR, frozenset()))
    return SourceStatusReader(
        sources=HeatSourceStoreStub(sources=[source], errors={}),
        units=units,
        temperatures=TemperatureStateStore(unavailable_after=timedelta(minutes=10), clock=clock),
        climate_settings=ClimateSensorsStoreStub(sensors=ClimateSensors(outdoor_slug=None)),
        gate=SourceLoadGateStub(held=held, gated=frozenset(), answers={}),
        book=ControlBook(),
        dry_run=False,
    )


@pytest.fixture
def f_ac_reader(request: pytest.FixtureRequest, f_book: ControlBook, clock: MonotonicClockStub) -> SourceStatusReader:
    temperatures = TemperatureStateStore(unavailable_after=timedelta(minutes=10), clock=clock)
    temperatures.record_state(
        'outdoor',
        TemperatureReading(temperature=Decimal(request.param), humidity=None, read_at=NOW),
    )
    units = ClimateUnitStateStore(unavailable_after=timedelta(minutes=10), clock=clock)
    units.record_state('ac-bedroom', AC_HEATING)
    return SourceStatusReader(
        sources=HeatSourceStoreStub(sources=[AIR_CONDITIONER], errors={}),
        units=units,
        temperatures=temperatures,
        climate_settings=ClimateSensorsStoreStub(sensors=ClimateSensors(outdoor_slug='outdoor')),
        gate=SourceLoadGateStub(held=frozenset(), gated=frozenset(), answers={}),
        book=f_book,
        dry_run=False,
    )


def test_status_gives_what_the_unit_reads_and_the_target_teplo_would_give_it(
    f_reader: SourceStatusReader,
    intents: RoomIntents,
) -> None:
    assert f_reader.statuses(intents) == {
        'office': (
            SourceStatus(
                slug='convector-office',
                name='Конвектор: кабинет',
                kind=HeatSourceKind.HEATER,
                room='office',
                device='heater-office',
                priority=1,
                live=False,
                available=True,
                error_code=None,
                error=None,
                on=True,
                setpoint=Decimal(35),
                level='High',
                temperature=Decimal(21),
                target_on=True,
                target_setpoint=Decimal(22),
                commands_live=False,
                held_until=None,
                shed=False,
                pending=PendingWrite(kind=PendingKind.WATCHING, at=None),
                mode=ClimateMode.HEAT,
            ),
        ),
    }


@pytest.mark.parametrize(
    ('f_reader', 'expected'),
    [
        (
            (LIVE_CONVECTOR, frozenset()),
            (False, None, PendingWrite(kind=PendingKind.NEXT_WRITE, at=NOW + timedelta(minutes=3))),
        ),
        ((LIVE_CONVECTOR, frozenset({'convector-office'})), (True, None, PendingWrite(kind=PendingKind.HELD, at=None))),
        (
            (replace(LIVE_CONVECTOR, manual_until=NOW + timedelta(hours=1)), frozenset()),
            (
                False,
                NOW + timedelta(hours=1),
                PendingWrite(kind=PendingKind.HANDS_OFF, at=NOW + timedelta(hours=1)),
            ),
        ),
    ],
    ids=['next_allowed_write', 'shed', 'held_by_hand'],
    indirect=['f_reader'],
)
def test_status_names_when_the_target_reaches_a_live_unit_or_what_holds_it_back(
    f_reader: SourceStatusReader,
    intents: RoomIntents,
    expected: tuple[bool, datetime | None, PendingWrite],
) -> None:
    status = f_reader.statuses(intents)['office'][0]

    assert (status.shed, status.held_until, status.pending) == expected


@pytest.mark.parametrize(
    'f_reader',
    [(replace(WATCHED_CONVECTOR, device_slug='heater-attic'), frozenset())],
    indirect=True,
)
def test_status_of_a_unit_never_read_says_it_is_unreachable(
    f_reader: SourceStatusReader,
    intents: RoomIntents,
) -> None:
    status = f_reader.statuses(intents)['office'][0]

    assert (status.available, status.on, status.target_setpoint, status.pending) == (
        False,
        None,
        None,
        PendingWrite(kind=PendingKind.UNREACHED, at=None),
    )


@pytest.mark.parametrize(
    ('f_ac_reader', 'f_book', 'expected'),
    [
        ('-16.0', False, (ClimateMode.HEAT, True, Decimal('-16.0'), None, False)),
        ('-14.5', True, (ClimateMode.HEAT, True, Decimal('-14.5'), None, True)),
        ('-14.5', False, (ClimateMode.HEAT, False, Decimal('-14.5'), True, False)),
    ],
    ids=['below_the_limit', 'locked_within_a_degree_above_it', 'unlocked_within_a_degree_above_it'],
    indirect=['f_ac_reader', 'f_book'],
)
def test_status_reads_a_source_s_lockout_from_the_book_without_remembering_it(
    f_ac_reader: SourceStatusReader,
    f_book: ControlBook,
    f_bedroom_intents: RoomIntents,
    expected: tuple[ClimateMode, bool, Decimal, bool | None, bool],
) -> None:
    status = f_ac_reader.statuses(f_bedroom_intents)['bedroom'][0]

    assert (
        status.mode,
        status.locked_out,
        status.outdoor,
        status.target_on,
        f_book.source_locked_out('ac-bedroom'),
    ) == expected
