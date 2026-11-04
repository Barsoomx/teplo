import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.climate.services.write_gate import KeyedWriteGate
from apps.core.stubs import MonotonicClockStub
from apps.devices.domain.errors import PlugNotSwitchableError
from apps.devices.domain.readings import PlugState
from apps.devices.stubs import SwitchablePlugProviderStub, SwitchablePlugStub
from apps.power.domain.loads import LoadKind, LoadPhase, LoadRecord
from apps.power.domain.shedding import HandBack
from apps.power.services.load_switcher import LoadSwitcher
from apps.power.stubs import PermitStub, SourceSwitchStub, ZoneSwitchStub

NOW = datetime(2026, 11, 2, 18, 40, tzinfo=UTC)
LOADS_READ_AT = 0.0
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
    live=True,
    shed=None,
)
KITCHEN = replace(
    BOILER,
    slug='kitchen',
    name='Кухня',
    kind=LoadKind.ZONE,
    zone_slug='kitchen',
    device_slug='thermostat-kitchen',
    priority=5,
)
CONVECTOR = replace(
    BOILER,
    slug='convector-office',
    name='Конвектор: кабинет',
    kind=LoadKind.SOURCE,
    device_slug='heater-office',
    priority=8,
    source_slug='convector-office',
    write_interval=timedelta(minutes=5),
    last_command_at=NOW - timedelta(minutes=10),
)
BOILER_MOVED = replace(BOILER, device_slug='plug-boiler-2f')
KITCHEN_MOVED = replace(KITCHEN, device_slug='thermostat-kitchen-new')


@pytest.fixture
def f_plug() -> SwitchablePlugStub:
    return SwitchablePlugStub(
        state=PlugState(
            on=True,
            power_watts=Decimal('1305.4'),
            current_amps=Decimal('6.02'),
            voltage_volts=Decimal('216.8'),
            read_at=NOW,
        ),
    )


@pytest.fixture
def f_other_plug() -> SwitchablePlugStub:
    return SwitchablePlugStub(state=None)


@pytest.fixture
def writes() -> KeyedWriteGate:
    return KeyedWriteGate(clock=MonotonicClockStub(now=0.0), key_lock=threading.Lock)


@pytest.fixture
def f_zones(writes: KeyedWriteGate) -> ZoneSwitchStub:
    return ZoneSwitchStub(writes=writes, error=None)


@pytest.fixture
def source_writes() -> KeyedWriteGate:
    return KeyedWriteGate(clock=MonotonicClockStub(now=0.0), key_lock=threading.Lock)


@pytest.fixture
def f_sources(source_writes: KeyedWriteGate) -> SourceSwitchStub:
    return SourceSwitchStub(writes=source_writes, error=None)


@pytest.fixture
def f_sources_written_a_minute_ago(source_writes: KeyedWriteGate) -> SourceSwitchStub:
    source_writes.reserve(
        'convector-office',
        NOW - timedelta(minutes=1),
        stamped_at=None,
        read_at=LOADS_READ_AT,
        interval=timedelta(minutes=5),
    )
    return SourceSwitchStub(writes=source_writes, error=None)


@pytest.fixture
def f_zones_written_a_minute_ago(writes: KeyedWriteGate) -> ZoneSwitchStub:
    writes.reserve('kitchen', NOW - timedelta(minutes=1), stamped_at=None, read_at=LOADS_READ_AT)
    return ZoneSwitchStub(writes=writes, error=None)


@pytest.fixture
def f_switcher(f_plug: SwitchablePlugStub, f_zones: ZoneSwitchStub, f_sources: SourceSwitchStub) -> LoadSwitcher:
    return LoadSwitcher(
        plugs=SwitchablePlugProviderStub(plugs={'plug-boiler-1f': f_plug}),
        zones=f_zones,
        sources=f_sources,
    )


@pytest.fixture
def f_switcher_of_two_plugs(
    f_plug: SwitchablePlugStub,
    f_other_plug: SwitchablePlugStub,
    f_zones: ZoneSwitchStub,
    f_sources: SourceSwitchStub,
) -> LoadSwitcher:
    return LoadSwitcher(
        plugs=SwitchablePlugProviderStub(plugs={'plug-boiler-1f': f_plug, 'plug-boiler-2f': f_other_plug}),
        zones=f_zones,
        sources=f_sources,
    )


@pytest.fixture
def f_switcher_without_plugs(f_zones: ZoneSwitchStub, f_sources: SourceSwitchStub) -> LoadSwitcher:
    return LoadSwitcher(plugs=SwitchablePlugProviderStub(plugs={}), zones=f_zones, sources=f_sources)


@pytest.fixture
def f_switcher_whose_zone_stamps_fail(
    f_plug: SwitchablePlugStub,
    writes: KeyedWriteGate,
    source_writes: KeyedWriteGate,
) -> LoadSwitcher:
    return LoadSwitcher(
        plugs=SwitchablePlugProviderStub(plugs={'plug-boiler-1f': f_plug}),
        zones=ZoneSwitchStub(writes=writes, error=None, stamped=False),
        sources=SourceSwitchStub(writes=source_writes, error=None, stamped=False),
    )


@pytest.fixture
def f_switcher_of_a_zone_written_a_minute_ago(
    f_plug: SwitchablePlugStub,
    f_zones_written_a_minute_ago: ZoneSwitchStub,
    f_sources: SourceSwitchStub,
) -> LoadSwitcher:
    return LoadSwitcher(
        plugs=SwitchablePlugProviderStub(plugs={'plug-boiler-1f': f_plug}),
        zones=f_zones_written_a_minute_ago,
        sources=f_sources,
    )


@pytest.fixture
def f_switcher_of_a_source_written_a_minute_ago(
    f_zones: ZoneSwitchStub,
    f_sources_written_a_minute_ago: SourceSwitchStub,
) -> LoadSwitcher:
    return LoadSwitcher(
        plugs=SwitchablePlugProviderStub(plugs={}),
        zones=f_zones,
        sources=f_sources_written_a_minute_ago,
    )


@pytest.fixture
def f_permit(request: pytest.FixtureRequest) -> PermitStub:
    return PermitStub(granted=request.param)


@pytest.fixture
def f_granted_permit() -> PermitStub:
    return PermitStub(granted=True)


def test_shedding_a_plug_switches_it_off(
    f_switcher: LoadSwitcher,
    f_plug: SwitchablePlugStub,
    f_zones: ZoneSwitchStub,
) -> None:
    stamped = f_switcher.shed(BOILER, NOW)

    assert (stamped, f_plug.switches, f_zones.switched_off) == (True, [False], [])


def test_shedding_a_zone_switches_its_thermostat_off(
    f_switcher: LoadSwitcher,
    f_plug: SwitchablePlugStub,
    f_zones: ZoneSwitchStub,
) -> None:
    stamped = f_switcher.shed(KITCHEN, NOW)

    assert (stamped, f_plug.switches, f_zones.switched_off) == (
        True,
        [],
        [('kitchen', 'thermostat-kitchen', NOW)],
    )


def test_shedding_a_heat_source_switches_its_unit_off_through_the_source_switch(
    f_switcher: LoadSwitcher,
    f_zones: ZoneSwitchStub,
    f_sources: SourceSwitchStub,
) -> None:
    stamped = f_switcher.shed(CONVECTOR, NOW)

    assert (stamped, f_zones.switched_off, f_sources.switched_off) == (
        True,
        [],
        [('convector-office', 'heater-office', NOW)],
    )


@pytest.mark.parametrize(
    ('load', 'expected'),
    [(BOILER, True), (KITCHEN, False), (CONVECTOR, False)],
    ids=['plug', 'zone', 'heat_source'],
)
def test_shedding_says_whether_the_write_stamp_a_zone_needs_was_stored(
    f_switcher_whose_zone_stamps_fail: LoadSwitcher,
    load: LoadRecord,
    expected: bool,
) -> None:
    assert f_switcher_whose_zone_stamps_fail.shed(load, NOW) is expected


@pytest.mark.parametrize(
    ('f_permit', 'restored', 'switches'),
    [(True, True, [True]), (False, False, [])],
    ids=['permit_granted', 'permit_refused'],
    indirect=['f_permit'],
)
def test_restoring_a_plug_teplo_switched_off_switches_it_on_with_a_permit(
    f_switcher: LoadSwitcher,
    f_plug: SwitchablePlugStub,
    f_permit: PermitStub,
    restored: bool,
    switches: list[bool],
) -> None:
    outcome = f_switcher.restore(BOILER, device='plug-boiler-1f', was_on=True, permit=f_permit)

    assert (outcome, f_plug.switches, f_permit.asked) == (restored, switches, 1)


@pytest.mark.parametrize(
    ('load', 'was_on'),
    [(BOILER, None), (KITCHEN, None), (CONVECTOR, True)],
    ids=['plug_teplo_did_not_switch_off', 'zone', 'heat_source'],
)
def test_restoring_switches_nothing_and_asks_no_permit_but_for_a_plug_teplo_switched_off(
    f_switcher: LoadSwitcher,
    f_plug: SwitchablePlugStub,
    f_zones: ZoneSwitchStub,
    f_sources: SourceSwitchStub,
    f_granted_permit: PermitStub,
    load: LoadRecord,
    was_on: bool | None,
) -> None:
    outcome = f_switcher.restore(load, device=load.device_slug, was_on=was_on, permit=f_granted_permit)

    assert (outcome, f_plug.switches, f_zones.switched_on, f_sources.switched_on, f_granted_permit.asked) == (
        True,
        [],
        [],
        [],
        0,
    )


def test_restoring_a_plug_without_a_switching_driver_fails_before_asking_a_permit(
    f_switcher_without_plugs: LoadSwitcher,
    f_granted_permit: PermitStub,
) -> None:
    with pytest.raises(PlugNotSwitchableError):
        f_switcher_without_plugs.restore(BOILER, device='plug-boiler-1f', was_on=True, permit=f_granted_permit)

    assert f_granted_permit.asked == 0


@pytest.mark.parametrize(
    ('load', 'was_on', 'cycle_writes', 'outcome', 'switches', 'switched_on'),
    [
        (BOILER, True, True, HandBack.SWITCHED_ON, [True], []),
        (BOILER, None, True, HandBack.UNTOUCHED, [], []),
        (KITCHEN, None, True, HandBack.UNTOUCHED, [], []),
        (KITCHEN, None, False, HandBack.SWITCHED_ON, [], [('kitchen', 'thermostat-kitchen', NOW)]),
    ],
    ids=[
        'plug_teplo_switched_off',
        'plug_teplo_did_not_switch_off',
        'zone_the_cycle_writes',
        'zone_the_cycle_no_longer_writes',
    ],
)
def test_releasing_hands_a_load_back_as_teplo_found_it(
    f_switcher: LoadSwitcher,
    f_plug: SwitchablePlugStub,
    f_zones: ZoneSwitchStub,
    load: LoadRecord,
    was_on: bool | None,
    cycle_writes: bool,
    outcome: HandBack,
    switches: list[bool],
    switched_on: list[tuple[str, str, datetime]],
) -> None:
    released = f_switcher.release(
        load,
        device=load.device_slug,
        was_on=was_on,
        cycle_writes=cycle_writes,
        at=NOW,
        read_at=LOADS_READ_AT,
    )

    assert (released, f_plug.switches, f_zones.switched_on) == (outcome, switches, switched_on)


def test_releasing_a_heat_source_switches_it_back_on_as_nobody_else_drives_it(
    f_switcher: LoadSwitcher,
    f_sources: SourceSwitchStub,
) -> None:
    released = f_switcher.release(
        CONVECTOR,
        device='heater-office',
        was_on=True,
        cycle_writes=False,
        at=NOW,
        read_at=LOADS_READ_AT,
    )

    assert (released, f_sources.switched_on) == (
        HandBack.SWITCHED_ON,
        [('convector-office', 'heater-office', NOW)],
    )


def test_release_of_a_heat_source_waits_for_its_own_write_interval(
    f_switcher_of_a_source_written_a_minute_ago: LoadSwitcher,
    f_sources_written_a_minute_ago: SourceSwitchStub,
) -> None:
    released = f_switcher_of_a_source_written_a_minute_ago.release(
        CONVECTOR,
        device='heater-office',
        was_on=True,
        cycle_writes=False,
        at=NOW,
        read_at=LOADS_READ_AT,
    )

    assert (released, f_sources_written_a_minute_ago.switched_on) == (HandBack.WAITING, [])


def test_release_of_a_zone_waits_while_the_zones_write_budget_is_spent(
    f_switcher_of_a_zone_written_a_minute_ago: LoadSwitcher,
    f_zones_written_a_minute_ago: ZoneSwitchStub,
) -> None:
    released = f_switcher_of_a_zone_written_a_minute_ago.release(
        KITCHEN,
        device='thermostat-kitchen',
        was_on=None,
        cycle_writes=False,
        at=NOW,
        read_at=LOADS_READ_AT,
    )

    assert (released, f_zones_written_a_minute_ago.switched_on) == (HandBack.WAITING, [])


def test_restore_switches_back_on_the_plug_the_shed_switched_off_not_the_one_the_load_points_at_now(
    f_switcher_of_two_plugs: LoadSwitcher,
    f_plug: SwitchablePlugStub,
    f_other_plug: SwitchablePlugStub,
    f_granted_permit: PermitStub,
) -> None:
    f_switcher_of_two_plugs.restore(BOILER_MOVED, device='plug-boiler-1f', was_on=True, permit=f_granted_permit)

    assert (f_plug.switches, f_other_plug.switches) == ([True], [])


@pytest.mark.parametrize(
    ('load', 'device', 'was_on', 'switches', 'switched_on'),
    [
        (BOILER_MOVED, 'plug-boiler-1f', True, [True], []),
        (KITCHEN_MOVED, 'thermostat-kitchen', None, [], [('kitchen', 'thermostat-kitchen', NOW)]),
    ],
    ids=['plug', 'zone'],
)
def test_release_switches_back_on_the_device_the_shed_switched_off_not_the_one_the_load_points_at_now(
    f_switcher_of_two_plugs: LoadSwitcher,
    f_plug: SwitchablePlugStub,
    f_other_plug: SwitchablePlugStub,
    f_zones: ZoneSwitchStub,
    load: LoadRecord,
    device: str,
    was_on: bool | None,
    switches: list[bool],
    switched_on: list[tuple[str, str, datetime]],
) -> None:
    released = f_switcher_of_two_plugs.release(
        load,
        device=device,
        was_on=was_on,
        cycle_writes=False,
        at=NOW,
        read_at=LOADS_READ_AT,
    )

    assert (released, f_plug.switches, f_other_plug.switches, f_zones.switched_on) == (
        HandBack.SWITCHED_ON,
        switches,
        [],
        switched_on,
    )


def test_plug_without_a_switching_driver_fails_the_shed(f_switcher_without_plugs: LoadSwitcher) -> None:
    with pytest.raises(PlugNotSwitchableError):
        f_switcher_without_plugs.shed(BOILER, NOW)
