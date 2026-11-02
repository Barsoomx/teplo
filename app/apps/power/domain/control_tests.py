from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from apps.power.domain.control import LoadControl, PhaseThresholds, commands_live, starts_live
from apps.power.domain.loads import LoadKind, LoadPhase, LoadRecord

CONTROL = LoadControl(
    live=True,
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
    live=True,
    shed=None,
)
KITCHEN = replace(BOILER, slug='kitchen', kind=LoadKind.ZONE, zone_slug='kitchen', device_slug='thermostat-kitchen')


@pytest.mark.parametrize(
    ('dry_run', 'management_live', 'load', 'zone_live_control', 'expected'),
    [
        (False, True, BOILER, False, True),
        (True, True, BOILER, True, False),
        (False, False, BOILER, True, False),
        (False, True, replace(BOILER, live=False), True, False),
        (False, True, KITCHEN, True, True),
        (False, True, KITCHEN, False, False),
    ],
    ids=[
        'plug_with_every_gate_open',
        'teplo_in_dry_run',
        'load_management_off',
        'load_off',
        'zone_with_live_control',
        'zone_without_live_control',
    ],
)
def test_commands_reach_a_load_only_through_every_gate(
    dry_run: bool,
    management_live: bool,
    load: LoadRecord,
    zone_live_control: bool,
    expected: bool,
) -> None:
    control = replace(CONTROL, live=management_live)

    assert commands_live(control, load, dry_run=dry_run, zone_live_control=zone_live_control) is expected


@pytest.mark.parametrize(
    ('writes_live', 'management_live', 'load', 'expected'),
    [
        (True, True, KITCHEN, True),
        (False, True, KITCHEN, False),
        (True, False, KITCHEN, False),
        (True, True, replace(KITCHEN, live=False), False),
    ],
    ids=['every_gate_open', 'zone_writes_held_back', 'load_management_off', 'load_off'],
)
def test_start_of_a_zone_waits_for_a_permit_only_when_its_write_and_its_load_are_live(
    writes_live: bool,
    management_live: bool,
    load: LoadRecord,
    expected: bool,
) -> None:
    control = replace(CONTROL, live=management_live)

    assert starts_live(control, load, writes_live=writes_live) is expected
