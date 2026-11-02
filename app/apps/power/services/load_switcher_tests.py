from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from apps.devices.domain.errors import PlugNotSwitchableError
from apps.devices.domain.readings import PlugState
from apps.devices.stubs import SwitchablePlugProviderStub, SwitchablePlugStub
from apps.power.domain.loads import LoadKind, LoadPhase, LoadRecord
from apps.power.services.load_switcher import LoadSwitcher
from apps.power.stubs import PermitStub, ZoneSwitchStub

NOW = datetime(2026, 11, 2, 18, 40, tzinfo=UTC)
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
def f_zones() -> ZoneSwitchStub:
    return ZoneSwitchStub(error=None)


@pytest.fixture
def f_switcher(f_plug: SwitchablePlugStub, f_zones: ZoneSwitchStub) -> LoadSwitcher:
    return LoadSwitcher(plugs=SwitchablePlugProviderStub(plugs={'plug-boiler-1f': f_plug}), zones=f_zones)


@pytest.fixture
def f_switcher_without_plugs(f_zones: ZoneSwitchStub) -> LoadSwitcher:
    return LoadSwitcher(plugs=SwitchablePlugProviderStub(plugs={}), zones=f_zones)


@pytest.fixture
def f_switcher_whose_zone_stamps_fail(f_plug: SwitchablePlugStub) -> LoadSwitcher:
    return LoadSwitcher(
        plugs=SwitchablePlugProviderStub(plugs={'plug-boiler-1f': f_plug}),
        zones=ZoneSwitchStub(error=None, stamped=False),
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


@pytest.mark.parametrize(('load', 'expected'), [(BOILER, True), (KITCHEN, False)], ids=['plug', 'zone'])
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
    outcome = f_switcher.restore(BOILER, was_on=True, permit=f_permit)

    assert (outcome, f_plug.switches, f_permit.asked) == (restored, switches, 1)


@pytest.mark.parametrize('load', [BOILER, KITCHEN], ids=['plug_teplo_did_not_switch_off', 'zone'])
def test_restoring_switches_nothing_and_asks_no_permit_but_for_a_plug_teplo_switched_off(
    f_switcher: LoadSwitcher,
    f_plug: SwitchablePlugStub,
    f_zones: ZoneSwitchStub,
    f_granted_permit: PermitStub,
    load: LoadRecord,
) -> None:
    outcome = f_switcher.restore(load, was_on=None, permit=f_granted_permit)

    assert (outcome, f_plug.switches, f_zones.switched_on, f_granted_permit.asked) == (True, [], [], 0)


def test_restoring_a_plug_without_a_switching_driver_fails_before_asking_a_permit(
    f_switcher_without_plugs: LoadSwitcher,
    f_granted_permit: PermitStub,
) -> None:
    with pytest.raises(PlugNotSwitchableError):
        f_switcher_without_plugs.restore(BOILER, was_on=True, permit=f_granted_permit)

    assert f_granted_permit.asked == 0


@pytest.mark.parametrize(
    ('load', 'was_on', 'zone_writes_live', 'switches', 'switched_on'),
    [
        (BOILER, True, True, [True], []),
        (BOILER, None, True, [], []),
        (KITCHEN, None, True, [], []),
        (KITCHEN, None, False, [], [('kitchen', 'thermostat-kitchen', NOW)]),
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
    zone_writes_live: bool,
    switches: list[bool],
    switched_on: list[tuple[str, str, datetime]],
) -> None:
    f_switcher.release(load, was_on=was_on, zone_writes_live=zone_writes_live, at=NOW)

    assert (f_plug.switches, f_zones.switched_on) == (switches, switched_on)


def test_plug_without_a_switching_driver_fails_the_shed(f_switcher_without_plugs: LoadSwitcher) -> None:
    with pytest.raises(PlugNotSwitchableError):
        f_switcher_without_plugs.shed(BOILER, NOW)
