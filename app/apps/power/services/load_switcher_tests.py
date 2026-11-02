from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from apps.devices.domain.errors import PlugNotSwitchableError
from apps.devices.domain.readings import PlugState
from apps.devices.stubs import SwitchablePlugProviderStub, SwitchablePlugStub
from apps.power.domain.loads import LoadKind, LoadPhase, LoadRecord
from apps.power.services.load_switcher import LoadSwitcher
from apps.power.stubs import ZoneShutoffStub

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
def f_zones() -> ZoneShutoffStub:
    return ZoneShutoffStub(error=None)


@pytest.fixture
def f_switcher(f_plug: SwitchablePlugStub, f_zones: ZoneShutoffStub) -> LoadSwitcher:
    return LoadSwitcher(plugs=SwitchablePlugProviderStub(plugs={'plug-boiler-1f': f_plug}), zones=f_zones)


def test_shedding_a_plug_switches_it_off(
    f_switcher: LoadSwitcher,
    f_plug: SwitchablePlugStub,
    f_zones: ZoneShutoffStub,
) -> None:
    f_switcher.shed(BOILER, NOW)

    assert (f_plug.switches, f_zones.switched_off) == ([False], [])


def test_shedding_a_zone_switches_its_thermostat_off(
    f_switcher: LoadSwitcher,
    f_plug: SwitchablePlugStub,
    f_zones: ZoneShutoffStub,
) -> None:
    f_switcher.shed(KITCHEN, NOW)

    assert (f_plug.switches, f_zones.switched_off) == ([], [('kitchen', 'thermostat-kitchen', NOW)])


@pytest.mark.parametrize(
    ('load', 'was_on', 'switches'),
    [(BOILER, True, [True]), (BOILER, None, []), (KITCHEN, None, [])],
    ids=['plug_teplo_switched_off', 'plug_teplo_did_not_switch_off', 'zone'],
)
def test_restoring_switches_on_only_a_plug_teplo_switched_off(
    f_switcher: LoadSwitcher,
    f_plug: SwitchablePlugStub,
    f_zones: ZoneShutoffStub,
    load: LoadRecord,
    was_on: bool | None,
    switches: list[bool],
) -> None:
    f_switcher.restore(load, was_on=was_on)

    assert (f_plug.switches, f_zones.switched_off) == (switches, [])


def test_plug_without_a_switching_driver_fails_the_shed(f_zones: ZoneShutoffStub) -> None:
    switcher = LoadSwitcher(plugs=SwitchablePlugProviderStub(plugs={}), zones=f_zones)

    with pytest.raises(PlugNotSwitchableError):
        switcher.shed(BOILER, NOW)
