from dataclasses import replace
from decimal import Decimal

import pytest

from apps.power.domain.ev_detection import EvDetection, EvWatch, detect_ev
from apps.power.domain.loads import LoadPhase
from apps.power.domain.phase_learning import LoadChange
from apps.power.domain.phase_samples import PhaseSample

L1 = Decimal('8.0')
L2 = Decimal('5.0')
RISE_SEEN_AT = 1003.0
WATCHED = EvWatch(
    phase=LoadPhase.L3,
    since=988.0,
    until=RISE_SEEN_AT,
    before=Decimal('4.0'),
    step=Decimal('16.0'),
    charging=False,
)
RISING = replace(WATCHED, until=1001.0, step=Decimal('11.0'))
CHARGING = replace(WATCHED, charging=True)
NOTHING = EvDetection(watches=(), started=(), stopped=())


def _samples(first: int, last: int, levels: dict[int, str]) -> list[PhaseSample]:
    return [
        PhaseSample(
            at=float(second),
            amps={
                LoadPhase.L1: L1,
                LoadPhase.L2: L2,
                LoadPhase.L3: Decimal(levels[max(start for start in levels if start <= second)]),
            },
        )
        for second in range(first, last + 1)
    ]


@pytest.fixture
def f_rise_on_l3() -> list[PhaseSample]:
    return _samples(980, 1003, {980: '4.0', 1000: '20.0'})


@pytest.fixture
def f_level_five_minutes_on() -> list[PhaseSample]:
    return _samples(1260, 1288, {1260: '20.0'})


@pytest.fixture
def f_level_after_the_rise() -> list[PhaseSample]:
    return _samples(1010, 1030, {1010: '20.0'})


@pytest.fixture
def f_early_drop() -> list[PhaseSample]:
    return _samples(1080, 1103, {1080: '20.0', 1100: '5.0'})


@pytest.fixture
def f_smaller_drop() -> list[PhaseSample]:
    return _samples(1980, 2003, {1980: '26.0', 2000: '20.0'})


@pytest.fixture
def f_slow_rise() -> list[PhaseSample]:
    return _samples(990, 1010, {990: '4.0', 1000: '15.0', 1005: '20.0'})


@pytest.fixture
def f_samples(request: pytest.FixtureRequest) -> list[PhaseSample]:
    return request.param


def test_a_rise_no_load_change_explains_is_watched(f_rise_on_l3: list[PhaseSample]) -> None:
    assert detect_ev([], f_rise_on_l3, [], RISE_SEEN_AT) == EvDetection(watches=(WATCHED,), started=(), stopped=())


def test_a_rise_a_load_change_explains_is_not_watched(f_rise_on_l3: list[PhaseSample]) -> None:
    changes = [LoadChange(load='boiler-2f', since=990.0, until=1020.0, up=True)]

    assert detect_ev([], f_rise_on_l3, changes, RISE_SEEN_AT) == NOTHING


@pytest.mark.parametrize(
    'f_samples',
    [_samples(980, 1003, {980: '4.0', 1000: '13.5'}), _samples(980, 1003, {980: '4.0', 1000: '26.5'})],
    ids=['under_ten_amperes', 'over_twenty_two_amperes'],
    indirect=True,
)
def test_a_rise_outside_the_charging_currents_is_not_watched(f_samples: list[PhaseSample]) -> None:
    assert detect_ev([], f_samples, [], RISE_SEEN_AT) == NOTHING


def test_a_rise_held_for_five_minutes_is_an_ev_charging(f_level_five_minutes_on: list[PhaseSample]) -> None:
    assert detect_ev([WATCHED], f_level_five_minutes_on, [], 1288.0) == EvDetection(
        watches=(CHARGING,),
        started=(CHARGING,),
        stopped=(),
    )


@pytest.mark.parametrize(
    'f_samples',
    [_samples(1980, 2003, {1980: '20.0', 2000: '6.5'}), _samples(1980, 2003, {1980: '26.0', 2000: '12.0'})],
    ids=['back_within_three_amperes', 'down_by_a_similar_step'],
    indirect=True,
)
def test_charging_stops_when_the_phase_drops_back(f_samples: list[PhaseSample]) -> None:
    assert detect_ev([CHARGING], f_samples, [], 2003.0) == EvDetection(watches=(), started=(), stopped=(CHARGING,))


def test_charging_goes_on_through_a_smaller_drop(f_smaller_drop: list[PhaseSample]) -> None:
    assert detect_ev([CHARGING], f_smaller_drop, [], 2003.0) == EvDetection(
        watches=(CHARGING,),
        started=(),
        stopped=(),
    )


def test_a_rise_that_drops_back_within_five_minutes_is_forgotten(f_early_drop: list[PhaseSample]) -> None:
    assert detect_ev([WATCHED], f_early_drop, [], 1103.0) == NOTHING


def test_a_rise_a_load_change_seen_later_explains_is_forgotten(f_level_after_the_rise: list[PhaseSample]) -> None:
    changes = [LoadChange(load='kitchen', since=995.0, until=1025.0, up=True)]

    assert detect_ev([WATCHED], f_level_after_the_rise, changes, 1030.0) == NOTHING


def test_a_slow_rise_grows_its_step(f_slow_rise: list[PhaseSample]) -> None:
    assert detect_ev([RISING], f_slow_rise, [], 1010.0) == EvDetection(
        watches=(replace(RISING, until=1010.0, step=Decimal('16.0')),),
        started=(),
        stopped=(),
    )


def test_watch_waits_for_samples() -> None:
    assert detect_ev([CHARGING], [], [], 2003.0) == EvDetection(watches=(CHARGING,), started=(), stopped=())
