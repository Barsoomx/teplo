from collections.abc import Collection, Mapping
from dataclasses import replace
from decimal import Decimal

import pytest

from apps.power.domain.load_status import LoadReading, plug_reading, thermostat_reading
from apps.power.domain.loads import MEASURED_PHASES, LearnedPhase, LoadPhase
from apps.power.domain.phase_learning import (
    LearningState,
    LearningStep,
    LoadChange,
    PhaseStep,
    PhaseVote,
    change_ripe,
    detect_change,
    find_step,
    follow_learning,
    learned_changed,
    remember_vote,
    tally_votes,
    vote_for_change,
)
from apps.power.domain.phase_samples import PhaseSample

BEFORE = 100.0
AFTER = 130.0
SWITCHED_AT = 115
BASE = {LoadPhase.L1: Decimal('10.0'), LoadPhase.L2: Decimal('5.0'), LoadPhase.L3: Decimal('3.0')}
NO_RISE: dict[LoadPhase, Decimal] = {}
SWITCHED_ON = LoadChange(load='boiler-2f', since=BEFORE, until=AFTER, up=True)
SETPOINT = Decimal('28.0')
FLOOR = Decimal('24.5')


def _samples(
    rise: Mapping[LoadPhase, Decimal],
    *,
    first: int = 90,
    missing: Collection[int] = (),
) -> list[PhaseSample]:
    return [
        PhaseSample(
            at=float(second),
            amps={
                phase: BASE[phase] + (rise.get(phase, Decimal(0)) if second >= SWITCHED_AT else Decimal(0))
                for phase in MEASURED_PHASES
            },
        )
        for second in range(first, 181)
        if second not in missing
    ]


def _plug(*, on: bool, current: str, read_at: float) -> LoadReading:
    return plug_reading(on=on, current=Decimal(current), read_at=read_at)


def _thermostat(*, heating: bool | None, read_at: float) -> LoadReading:
    return thermostat_reading(power=True, heating=heating, setpoint=SETPOINT, floor_temperature=FLOOR, read_at=read_at)


def _votes(*votes: tuple[LoadPhase, str]) -> list[PhaseVote]:
    return [PhaseVote(phase=phase, amps=Decimal(amps)) for phase, amps in votes]


@pytest.fixture
def f_step_on_l2() -> list[PhaseSample]:
    return _samples({LoadPhase.L2: Decimal('6.0')})


@pytest.fixture
def f_samples(request: pytest.FixtureRequest) -> list[PhaseSample]:
    return request.param


@pytest.fixture
def f_switch_waiting() -> LearningState:
    return LearningState(changes=(SWITCHED_ON,), pending=(SWITCHED_ON,))


@pytest.fixture
def f_switch_waiting_after_two_votes(f_switch_waiting: LearningState) -> LearningState:
    return replace(f_switch_waiting, votes={'boiler-2f': tuple(_votes((LoadPhase.L2, '6.0'), (LoadPhase.L2, '6.4')))})


@pytest.mark.parametrize(
    ('previous', 'current', 'expected'),
    [
        (
            _plug(on=False, current='0.0', read_at=BEFORE),
            _plug(on=True, current='6.1', read_at=AFTER),
            SWITCHED_ON,
        ),
        (
            _plug(on=True, current='6.1', read_at=BEFORE),
            _plug(on=False, current='0.0', read_at=AFTER),
            LoadChange(load='boiler-2f', since=BEFORE, until=AFTER, up=False),
        ),
        (_plug(on=True, current='0.2', read_at=BEFORE), _plug(on=True, current='6.1', read_at=AFTER), SWITCHED_ON),
        (
            _plug(on=True, current='6.1', read_at=BEFORE),
            _plug(on=True, current='4.1', read_at=AFTER),
            LoadChange(load='boiler-2f', since=BEFORE, until=AFTER, up=False),
        ),
        (_plug(on=True, current='6.1', read_at=BEFORE), _plug(on=True, current='4.2', read_at=AFTER), None),
        (
            _thermostat(heating=True, read_at=BEFORE),
            _thermostat(heating=False, read_at=AFTER),
            LoadChange(load='boiler-2f', since=BEFORE, until=AFTER, up=False),
        ),
        (_thermostat(heating=None, read_at=BEFORE), _thermostat(heating=None, read_at=AFTER), None),
        (_plug(on=False, current='0.0', read_at=AFTER), _plug(on=True, current='6.1', read_at=AFTER), None),
    ],
    ids=[
        'plug_switched_on',
        'plug_switched_off',
        'plug_current_rose_by_two_amperes',
        'plug_current_fell_by_two_amperes',
        'plug_current_moved_less',
        'thermostat_relay_went_idle',
        'thermostat_relay_unknown',
        'same_reading',
    ],
)
def test_change_is_a_switch_a_two_ampere_move_or_a_relay_turn_between_two_readings(
    previous: LoadReading,
    current: LoadReading,
    expected: LoadChange | None,
) -> None:
    assert detect_change('boiler-2f', previous, current) == expected


@pytest.mark.parametrize(('at', 'expected'), [(164.9, False), (165.0, True)])
def test_change_is_ripe_once_every_load_has_been_read_after_it(at: float, expected: bool) -> None:
    assert change_ripe(SWITCHED_ON, at) is expected


def test_strongest_step_is_found_between_the_readings(f_step_on_l2: list[PhaseSample]) -> None:
    assert find_step(f_step_on_l2, BEFORE, AFTER) == PhaseStep(
        phase=LoadPhase.L2,
        at=float(SWITCHED_AT),
        amps=Decimal('6.0'),
        others=Decimal(0),
    )


@pytest.mark.parametrize(
    'changes',
    [[SWITCHED_ON], [SWITCHED_ON, LoadChange(load='kitchen', since=AFTER, until=AFTER + 30, up=True)]],
    ids=['alone', 'next_to_a_change_after_the_window'],
)
def test_clean_step_votes_for_its_phase(f_step_on_l2: list[PhaseSample], changes: list[LoadChange]) -> None:
    assert vote_for_change(SWITCHED_ON, f_step_on_l2, changes) == PhaseVote(phase=LoadPhase.L2, amps=Decimal('6.0'))


@pytest.mark.parametrize(
    ('f_samples', 'change'),
    [
        (_samples({LoadPhase.L2: Decimal('1.2')}), SWITCHED_ON),
        (_samples({LoadPhase.L2: Decimal('6.0')}), LoadChange(load='boiler-2f', since=BEFORE, until=AFTER, up=False)),
        (_samples({LoadPhase.L2: Decimal('6.0'), LoadPhase.L1: Decimal('0.9')}), SWITCHED_ON),
        (_samples({LoadPhase.L2: Decimal('6.0')}, missing=tuple(range(118, 125))), SWITCHED_ON),
        (_samples({LoadPhase.L2: Decimal('6.0')}, first=99), SWITCHED_ON),
        (_samples(NO_RISE), SWITCHED_ON),
    ],
    ids=[
        'step_under_one_and_a_half_amperes',
        'step_against_the_change',
        'another_phase_moved',
        'gap_in_the_samples',
        'samples_start_too_late',
        'no_step',
    ],
    indirect=['f_samples'],
)
def test_step_that_does_not_clearly_follow_the_change_casts_no_vote(
    f_samples: list[PhaseSample],
    change: LoadChange,
) -> None:
    assert vote_for_change(change, f_samples, [change]) is None


def test_window_another_load_changed_in_casts_no_vote(f_step_on_l2: list[PhaseSample]) -> None:
    changes = [SWITCHED_ON, LoadChange(load='kitchen', since=BEFORE + 10, until=AFTER + 10, up=False)]

    assert vote_for_change(SWITCHED_ON, f_step_on_l2, changes) is None


def test_votes_keep_the_last_five() -> None:
    votes = _votes((LoadPhase.L1, '6.0'), *[(LoadPhase.L2, '6.2')] * 4)

    assert remember_vote(votes, PhaseVote(phase=LoadPhase.L2, amps=Decimal('6.4'))) == (
        *_votes(*[(LoadPhase.L2, '6.2')] * 4),
        PhaseVote(phase=LoadPhase.L2, amps=Decimal('6.4')),
    )


@pytest.mark.parametrize(
    ('votes', 'expected'),
    [
        (
            _votes((LoadPhase.L2, '6.0'), (LoadPhase.L2, '6.4'), (LoadPhase.L2, '5.8')),
            LearnedPhase(phase=LoadPhase.L2, amps=Decimal('6.0'), samples=3),
        ),
        (
            _votes((LoadPhase.L2, '6.0'), (LoadPhase.L2, '6.2'), (LoadPhase.L2, '6.6'), (LoadPhase.L2, '7.0')),
            LearnedPhase(phase=LoadPhase.L2, amps=Decimal('6.4'), samples=4),
        ),
        (_votes((LoadPhase.L2, '6.0'), (LoadPhase.L2, '6.2')), None),
        (_votes((LoadPhase.L1, '6.0'), *[(LoadPhase.L2, '6.2')] * 3), None),
        (
            _votes((LoadPhase.L1, '6.0'), *[(LoadPhase.L2, '6.24')] * 5),
            LearnedPhase(phase=LoadPhase.L2, amps=Decimal('6.2'), samples=5),
        ),
    ],
    ids=[
        'three_votes_for_one_phase',
        'median_of_an_even_count',
        'two_votes',
        'a_vote_against_among_the_last_five',
        'the_vote_against_left_the_last_five',
    ],
)
def test_phase_is_learned_after_three_votes_for_it_and_none_against_among_the_last_five(
    votes: list[PhaseVote],
    expected: LearnedPhase | None,
) -> None:
    assert tally_votes(votes) == expected


@pytest.mark.parametrize(
    ('stored', 'expected'),
    [
        (None, True),
        (LearnedPhase(phase=LoadPhase.L2, amps=Decimal('6.4'), samples=5), False),
        (LearnedPhase(phase=LoadPhase.L2, amps=Decimal('5.5'), samples=3), True),
        (LearnedPhase(phase=LoadPhase.L3, amps=Decimal('6.0'), samples=3), True),
    ],
    ids=['first_learned', 'amps_moved_a_little', 'amps_moved_half_an_ampere', 'phase_changed'],
)
def test_learned_phase_is_stored_again_only_when_it_changes(stored: LearnedPhase | None, expected: bool) -> None:
    assert learned_changed(stored, LearnedPhase(phase=LoadPhase.L2, amps=Decimal('6.0'), samples=4)) is expected


def test_change_waits_until_every_load_has_been_read_after_it(f_step_on_l2: list[PhaseSample]) -> None:
    step = follow_learning(LearningState(), [SWITCHED_ON], f_step_on_l2, 164.0)

    assert step == LearningStep(state=LearningState(changes=(SWITCHED_ON,), pending=(SWITCHED_ON,)), learned=())


def test_ripe_change_casts_its_vote(f_step_on_l2: list[PhaseSample], f_switch_waiting: LearningState) -> None:
    step = follow_learning(f_switch_waiting, [], f_step_on_l2, 165.0)

    assert step == LearningStep(
        state=LearningState(
            changes=(SWITCHED_ON,),
            votes={'boiler-2f': (PhaseVote(phase=LoadPhase.L2, amps=Decimal('6.0')),)},
        ),
        learned=(),
    )


def test_third_vote_for_one_phase_learns_it(
    f_step_on_l2: list[PhaseSample],
    f_switch_waiting_after_two_votes: LearningState,
) -> None:
    step = follow_learning(f_switch_waiting_after_two_votes, [], f_step_on_l2, 165.0)

    assert step.learned == (('boiler-2f', LearnedPhase(phase=LoadPhase.L2, amps=Decimal('6.0'), samples=3)),)


def test_changes_older_than_the_samples_are_forgotten(f_step_on_l2: list[PhaseSample]) -> None:
    state = LearningState(changes=(LoadChange(load='kitchen', since=10.0, until=40.0, up=True),))

    assert follow_learning(state, [], f_step_on_l2, 161.0).state.changes == ()
