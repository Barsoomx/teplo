from dataclasses import replace
from decimal import Decimal

import pytest

from apps.devices.domain.readings import Phase
from apps.power.domain.loads import (
    AmpsOrigin,
    LearnedPhase,
    LoadKind,
    LoadPhase,
    LoadRecord,
    PhaseOrigin,
    measured_currents,
    rated_amps,
)

DEFAULT_AMPS = Decimal('6.0')
LEARNED = LearnedPhase(phase=LoadPhase.L2, amps=Decimal('8.4'), samples=3)
KITCHEN = LoadRecord(
    slug='kitchen',
    name='Кухня',
    kind=LoadKind.ZONE,
    zone_slug='kitchen',
    device_slug='thermostat-kitchen',
    priority=5,
    phase=LoadPhase.UNKNOWN,
    nominal_amps=None,
    learned=None,
    live=False,
    shed=None,
)


@pytest.mark.parametrize(
    ('phase', 'learned', 'expected'),
    [
        (LoadPhase.L1, LEARNED, (LoadPhase.L1, PhaseOrigin.SET)),
        (LoadPhase.UNKNOWN, LEARNED, (LoadPhase.L2, PhaseOrigin.LEARNED)),
        (LoadPhase.UNKNOWN, None, (LoadPhase.UNKNOWN, PhaseOrigin.UNKNOWN)),
    ],
    ids=['set_by_the_owner', 'learned', 'neither'],
)
def test_effective_phase_is_the_owners_then_the_learned_one(
    phase: LoadPhase,
    learned: LearnedPhase | None,
    expected: tuple[LoadPhase, PhaseOrigin],
) -> None:
    load = replace(KITCHEN, phase=phase, learned=learned)

    assert (load.effective_phase, load.phase_origin) == expected


@pytest.mark.parametrize(
    ('nominal_amps', 'learned', 'rated', 'expected'),
    [
        (Decimal('7.5'), LEARNED, Decimal('8.7'), (Decimal('7.5'), AmpsOrigin.NOMINAL)),
        (None, LEARNED, Decimal('8.7'), (Decimal('8.4'), AmpsOrigin.LEARNED)),
        (None, None, Decimal('8.7'), (Decimal('8.7'), AmpsOrigin.RATED)),
        (None, None, None, (DEFAULT_AMPS, AmpsOrigin.DEFAULT)),
    ],
    ids=['nominal', 'learned', 'rated', 'default'],
)
def test_effective_amps_are_the_nominal_then_the_learned_then_the_rated_then_the_default_ones(
    nominal_amps: Decimal | None,
    learned: LearnedPhase | None,
    rated: Decimal | None,
    expected: tuple[Decimal, AmpsOrigin],
) -> None:
    load = replace(KITCHEN, nominal_amps=nominal_amps, learned=learned, rated_amps=rated)

    assert (load.effective_amps(DEFAULT_AMPS), load.amps_origin) == expected


def test_measured_currents_name_the_meter_phases_l1_to_l3() -> None:
    currents = measured_currents({Phase.A: Decimal('12.4'), Phase.B: Decimal('3.1'), Phase.C: Decimal('0.8')})

    assert currents == {LoadPhase.L1: Decimal('12.4'), LoadPhase.L2: Decimal('3.1'), LoadPhase.L3: Decimal('0.8')}


@pytest.mark.parametrize(
    ('watts', 'amps'),
    [(2000, Decimal('8.7')), (1000, Decimal('4.3')), (1500, Decimal('6.5'))],
    ids=['convector_at_high', 'convector_at_low', 'rounded_to_a_tenth'],
)
def test_rated_amps_divide_the_rated_power_by_230_volts_to_a_tenth(watts: int, amps: Decimal) -> None:
    assert rated_amps(watts) == amps
