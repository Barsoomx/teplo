from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from apps.power.domain.control import PhaseThresholds
from apps.power.domain.headroom import PhaseOverrun
from apps.power.domain.load_status import LoadStatus
from apps.power.domain.loads import LoadKind, LoadPhase
from apps.power.domain.meter_watch import MeterCondition
from apps.power.domain.start_gate import (
    StartDecision,
    StartRefusal,
    StartRequest,
    StartRound,
    decide_start,
    decide_starts,
    in_start_order,
)
from apps.power.domain.view import GuardView

THRESHOLDS = PhaseThresholds(
    start_ceiling=Decimal('20.0'),
    shed=Decimal('24.0'),
    shed_hold=timedelta(seconds=3),
    restore=Decimal('18.0'),
    restore_hold=timedelta(minutes=2),
    shed_interval=timedelta(seconds=1),
    restore_interval=timedelta(minutes=1),
    start_interval=timedelta(seconds=30),
    meter_lost_after=timedelta(seconds=60),
)
AT = 1000.0
CURRENTS = {LoadPhase.L1: Decimal('12.0'), LoadPhase.L2: Decimal('15.0'), LoadPhase.L3: Decimal('9.0')}
KITCHEN = LoadStatus(
    slug='kitchen',
    kind=LoadKind.ZONE,
    priority=5,
    phase=LoadPhase.L1,
    amps=Decimal('8.0'),
    available=True,
    drawing=False,
    shed=False,
)
HALLWAY = replace(KITCHEN, slug='hallway', priority=4, drawing=True)
BOILER = replace(KITCHEN, slug='boiler-1f', kind=LoadKind.PLUG, priority=1, amps=Decimal('6.0'))
GRANTED = StartDecision(load='kitchen', refusal=None, overrun=None)
LIVING_ROOM = replace(KITCHEN, slug='living-room', priority=7, phase=LoadPhase.L3)
GRANTED_LIVING_ROOM = StartDecision(load='living-room', refusal=None, overrun=None)
UNKNOWN_KITCHEN = replace(KITCHEN, phase=LoadPhase.UNKNOWN)


def _decide(
    load: LoadStatus,
    meter: MeterCondition,
    *,
    loads: list[LoadStatus] | None = None,
    last_permit_at: float | None = None,
) -> StartDecision:
    return decide_start(
        load,
        meter=meter,
        view=GuardView(
            currents=CURRENTS if meter is MeterCondition.FRESH else {},
            loads=(load,) if loads is None else tuple(loads),
        ),
        last_permit_at=last_permit_at,
        at=AT,
        thresholds=THRESHOLDS,
    )


@pytest.mark.parametrize(
    ('last_permit_at', 'expected'),
    [
        (None, GRANTED),
        (AT - 30, GRANTED),
        (AT - 29, StartDecision(load='kitchen', refusal=StartRefusal.TOO_SOON, overrun=None)),
    ],
    ids=['first_start', 'thirty_seconds_after_the_last_permit', 'within_thirty_seconds_of_the_last_permit'],
)
def test_start_on_a_fresh_meter_waits_thirty_seconds_after_the_last_permit(
    last_permit_at: float | None,
    expected: StartDecision,
) -> None:
    assert _decide(KITCHEN, MeterCondition.FRESH, last_permit_at=last_permit_at) == expected


@pytest.mark.parametrize(
    ('phase', 'amps', 'overrun'),
    [
        (LoadPhase.L2, Decimal('6.0'), PhaseOverrun(phase=LoadPhase.L2, amps=Decimal('21.0'))),
        (LoadPhase.UNKNOWN, Decimal('6.0'), PhaseOverrun(phase=LoadPhase.L2, amps=Decimal('21.0'))),
        (LoadPhase.L1, Decimal('8.5'), PhaseOverrun(phase=LoadPhase.L1, amps=Decimal('20.5'))),
    ],
    ids=['its_phase', 'unknown_phase_on_the_fullest_phase', 'by_half_an_ampere'],
)
def test_start_is_refused_when_it_would_push_a_phase_over_the_start_ceiling(
    phase: LoadPhase,
    amps: Decimal,
    overrun: PhaseOverrun,
) -> None:
    decision = _decide(replace(KITCHEN, phase=phase, amps=amps), MeterCondition.FRESH)

    assert (decision, decision.granted) == (
        StartDecision(load='kitchen', refusal=StartRefusal.NO_HEADROOM, overrun=overrun),
        False,
    )


@pytest.mark.parametrize('load', [KITCHEN, BOILER], ids=['zone', 'plug'])
def test_start_waits_while_the_meter_is_stale(load: LoadStatus) -> None:
    assert _decide(load, MeterCondition.STALE) == StartDecision(
        load=load.slug,
        refusal=StartRefusal.METER_STALE,
        overrun=None,
    )


def test_lost_meter_starts_no_plug() -> None:
    assert _decide(BOILER, MeterCondition.LOST) == StartDecision(
        load='boiler-1f',
        refusal=StartRefusal.METER_STALE,
        overrun=None,
    )


@pytest.mark.parametrize(
    ('others', 'expected'),
    [
        ([], GRANTED),
        ([HALLWAY], StartDecision(load='kitchen', refusal=StartRefusal.PHASE_TAKEN, overrun=None)),
        ([replace(HALLWAY, phase=LoadPhase.L2)], GRANTED),
        ([replace(HALLWAY, shed=True)], GRANTED),
        ([replace(BOILER, drawing=True)], GRANTED),
        ([replace(HALLWAY, phase=LoadPhase.UNKNOWN)], GRANTED),
    ],
    ids=[
        'no_floor_draws',
        'another_floor_draws_on_its_phase',
        'a_floor_draws_on_another_phase',
        'the_floor_on_its_phase_is_shed',
        'a_plug_draws_on_its_phase',
        'a_floor_of_unknown_phase_draws',
    ],
)
def test_lost_meter_starts_a_floor_only_when_no_other_floor_draws_on_its_phase(
    others: list[LoadStatus],
    expected: StartDecision,
) -> None:
    assert _decide(KITCHEN, MeterCondition.LOST, loads=[KITCHEN, *others]) == expected


@pytest.mark.parametrize(
    ('others', 'expected'),
    [
        ([], GRANTED),
        ([replace(HALLWAY, phase=LoadPhase.L3)], GRANTED),
        (
            [replace(HALLWAY, phase=LoadPhase.UNKNOWN)],
            StartDecision(load='kitchen', refusal=StartRefusal.PHASE_TAKEN, overrun=None),
        ),
        ([replace(BOILER, phase=LoadPhase.UNKNOWN, drawing=True)], GRANTED),
    ],
    ids=[
        'no_floor_draws',
        'a_floor_of_known_phase_draws',
        'another_floor_of_unknown_phase_draws',
        'a_plug_of_unknown_phase_draws',
    ],
)
def test_lost_meter_counts_floors_of_unknown_phase_as_one_more_phase(
    others: list[LoadStatus],
    expected: StartDecision,
) -> None:
    assert _decide(UNKNOWN_KITCHEN, MeterCondition.LOST, loads=[UNKNOWN_KITCHEN, *others]) == expected


def test_lost_meter_starts_floors_thirty_seconds_apart() -> None:
    assert _decide(KITCHEN, MeterCondition.LOST, last_permit_at=AT - 10) == StartDecision(
        load='kitchen',
        refusal=StartRefusal.TOO_SOON,
        overrun=None,
    )


def test_starts_are_served_most_important_first() -> None:
    assert in_start_order([BOILER, KITCHEN, HALLWAY]) == (KITCHEN, HALLWAY, BOILER)


def _serve(
    requests: list[StartRequest],
    last_permit_at: float | None = None,
    live_currents: dict[LoadPhase, Decimal] = CURRENTS,
    dry_run_currents: dict[LoadPhase, Decimal] = CURRENTS,
) -> StartRound:
    loads = tuple(request.load for request in requests)
    return decide_starts(
        requests,
        meter=MeterCondition.FRESH,
        live_view=GuardView(currents=live_currents, loads=loads),
        dry_run_view=GuardView(currents=dry_run_currents, loads=loads),
        last_permit_at=last_permit_at,
        at=AT,
        thresholds=THRESHOLDS,
    )


def test_round_serves_the_most_important_start_first_and_holds_back_the_rest() -> None:
    served = _serve([StartRequest(load=KITCHEN, live=True), StartRequest(load=LIVING_ROOM, live=True)])

    assert served == StartRound(
        decisions={
            'living-room': StartDecision(load='living-room', refusal=None, overrun=None),
            'kitchen': StartDecision(load='kitchen', refusal=StartRefusal.TOO_SOON, overrun=None),
        },
        last_permit_at=AT,
    )


def test_round_of_dry_run_starts_takes_no_permit_but_holds_back_the_starts_after_its_own() -> None:
    served = _serve([StartRequest(load=KITCHEN, live=False), StartRequest(load=LIVING_ROOM, live=False)])

    assert served == StartRound(
        decisions={
            'living-room': StartDecision(load='living-room', refusal=None, overrun=None),
            'kitchen': StartDecision(load='kitchen', refusal=StartRefusal.TOO_SOON, overrun=None),
        },
        last_permit_at=None,
    )


def test_dry_run_start_never_holds_back_a_live_one() -> None:
    served = _serve([StartRequest(load=KITCHEN, live=True), StartRequest(load=LIVING_ROOM, live=False)])

    assert served == StartRound(decisions={'living-room': GRANTED_LIVING_ROOM, 'kitchen': GRANTED}, last_permit_at=AT)


def test_live_permit_holds_back_the_dry_run_starts_after_it() -> None:
    served = _serve([StartRequest(load=KITCHEN, live=False), StartRequest(load=LIVING_ROOM, live=True)])

    assert served == StartRound(
        decisions={
            'living-room': GRANTED_LIVING_ROOM,
            'kitchen': StartDecision(load='kitchen', refusal=StartRefusal.TOO_SOON, overrun=None),
        },
        last_permit_at=AT,
    )


def test_round_keeps_the_last_permit_when_it_grants_none() -> None:
    served = _serve([StartRequest(load=KITCHEN, live=True)], last_permit_at=AT - 10)

    assert served == StartRound(
        decisions={'kitchen': StartDecision(load='kitchen', refusal=StartRefusal.TOO_SOON, overrun=None)},
        last_permit_at=AT - 10,
    )


def test_round_judges_a_live_start_on_the_live_view_and_a_dry_run_start_on_the_dry_run_view() -> None:
    served = _serve(
        [
            StartRequest(load=replace(KITCHEN, phase=LoadPhase.L2), live=True),
            StartRequest(load=LIVING_ROOM, live=False),
        ],
        live_currents={**CURRENTS, LoadPhase.L3: Decimal('15.0')},
        dry_run_currents={**CURRENTS, LoadPhase.L2: Decimal('9.0')},
    )

    assert served == StartRound(
        decisions={
            'living-room': GRANTED_LIVING_ROOM,
            'kitchen': StartDecision(
                load='kitchen',
                refusal=StartRefusal.NO_HEADROOM,
                overrun=PhaseOverrun(phase=LoadPhase.L2, amps=Decimal('23.0')),
            ),
        },
        last_permit_at=None,
    )
