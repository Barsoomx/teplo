from dataclasses import replace
from decimal import Decimal

import pytest

from apps.power.domain.ev_detection import EvDetection, EvWatch, detect_ev
from apps.power.domain.load_status import LoadStatus
from apps.power.domain.loads import LoadKind, LoadPhase, ShedReason
from apps.power.domain.phase_learning import LoadChange
from apps.power.domain.phase_samples import PhaseSample
from apps.power.domain.shedding import Shed

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
CHARGING_ON_A_BUSY_PHASE = replace(CHARGING, before=Decimal('18.0'))
CHARGING_BESIDE_A_FLOOR = replace(CHARGING, before=Decimal('12.5'), step=Decimal('11.0'), drop_seen_at=2003.0)
KITCHEN = LoadStatus(
    slug='kitchen',
    kind=LoadKind.ZONE,
    priority=5,
    phase=LoadPhase.L3,
    amps=Decimal('8.5'),
    available=True,
    drawing=False,
    shed=False,
)
KITCHEN_SWITCHED_OFF = LoadChange(load='kitchen', since=1990.0, until=2020.0, up=False)
NOTHING = EvDetection(watches=(), started=(), stopped=())
BOILER_SHED = Shed(
    load='boiler-2f',
    phase=LoadPhase.L3,
    amps=Decimal('6.0'),
    live=True,
    reason=ShedReason.OVERLOAD,
    was_on=True,
    at=2000.0,
    settled_at=2005.0,
)
KITCHEN_SHED = replace(BOILER_SHED, load='kitchen', amps=Decimal('8.0'), was_on=None, at=2000.5, settled_at=2005.5)


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
    return _samples(1080, 1138, {1080: '20.0', 1100: '5.0'})


@pytest.fixture
def f_drop_held_until_the_loads_were_read() -> list[PhaseSample]:
    return _samples(1980, 2038, {1980: '20.0', 2000: '6.5'})


@pytest.fixture
def f_floor_switching_off_beside_a_charge() -> list[PhaseSample]:
    return _samples(1980, 2038, {1980: '23.5', 2000: '15.0'})


@pytest.fixture
def f_charge_and_floor_stopping_together() -> list[PhaseSample]:
    return _samples(1980, 2038, {1980: '23.5', 2000: '4.0'})


@pytest.fixture
def f_smaller_drop() -> list[PhaseSample]:
    return _samples(1980, 2003, {1980: '26.0', 2000: '20.0'})


@pytest.fixture
def f_slow_rise() -> list[PhaseSample]:
    return _samples(990, 1010, {990: '4.0', 1000: '15.0', 1005: '20.0'})


@pytest.fixture
def f_rise_across_a_gap() -> list[PhaseSample]:
    return [*_samples(980, 990, {980: '4.0'}), *_samples(997, 1003, {997: '20.0'})]


@pytest.fixture
def f_rise_the_guard_sheds_into() -> list[PhaseSample]:
    return _samples(980, 1010, {980: '18.0', 1000: '34.0', 1003: '20.0'})


@pytest.fixture
def f_rise_while_a_shed_settles() -> list[PhaseSample]:
    return _samples(980, 1002, {980: '4.0', 1000: '14.0'})


@pytest.fixture
def f_charge_after_the_guards_sheds() -> list[PhaseSample]:
    return _samples(2020, 2043, {2020: '20.0', 2040: '4.0'})


@pytest.fixture
def f_samples(request: pytest.FixtureRequest) -> list[PhaseSample]:
    return request.param


def test_a_rise_no_load_change_explains_is_watched(f_rise_on_l3: list[PhaseSample]) -> None:
    assert detect_ev([], f_rise_on_l3, [], RISE_SEEN_AT, sheds=(), loads=()) == EvDetection(
        watches=(WATCHED,),
        started=(),
        stopped=(),
    )


def test_a_rise_a_load_change_explains_is_not_watched(f_rise_on_l3: list[PhaseSample]) -> None:
    changes = [LoadChange(load='boiler-2f', since=990.0, until=1020.0, up=True)]

    assert detect_ev([], f_rise_on_l3, changes, RISE_SEEN_AT, sheds=(), loads=()) == NOTHING


@pytest.mark.parametrize(
    'f_samples',
    [_samples(980, 1003, {980: '4.0', 1000: '13.5'}), _samples(980, 1003, {980: '4.0', 1000: '26.5'})],
    ids=['under_ten_amperes', 'over_twenty_two_amperes'],
    indirect=True,
)
def test_a_rise_outside_the_charging_currents_is_not_watched(f_samples: list[PhaseSample]) -> None:
    assert detect_ev([], f_samples, [], RISE_SEEN_AT, sheds=(), loads=()) == NOTHING


def test_a_rise_held_for_five_minutes_is_an_ev_charging(f_level_five_minutes_on: list[PhaseSample]) -> None:
    assert detect_ev([WATCHED], f_level_five_minutes_on, [], 1288.0, sheds=(), loads=()) == EvDetection(
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
def test_a_drop_back_is_held_until_every_load_has_been_read_after_it(f_samples: list[PhaseSample]) -> None:
    assert detect_ev([CHARGING], f_samples, [], 2003.0, sheds=(), loads=()) == EvDetection(
        watches=(replace(CHARGING, drop_seen_at=2003.0),),
        started=(),
        stopped=(),
    )


def test_charging_stops_once_no_load_seen_switching_off_explains_the_drop(
    f_drop_held_until_the_loads_were_read: list[PhaseSample],
) -> None:
    held = replace(CHARGING, drop_seen_at=2003.0)

    assert detect_ev([held], f_drop_held_until_the_loads_were_read, [], 2038.0, sheds=(), loads=()) == EvDetection(
        watches=(),
        started=(),
        stopped=(held,),
    )


def test_charging_goes_on_when_a_floor_seen_switching_off_on_the_phase_explains_the_drop(
    f_floor_switching_off_beside_a_charge: list[PhaseSample],
) -> None:
    detection = detect_ev(
        [CHARGING_BESIDE_A_FLOOR],
        f_floor_switching_off_beside_a_charge,
        [KITCHEN_SWITCHED_OFF],
        2038.0,
        sheds=(),
        loads=(KITCHEN,),
    )

    assert detection == EvDetection(
        watches=(replace(CHARGING_BESIDE_A_FLOOR, switched_off=Decimal('8.5'), drop_seen_at=None),),
        started=(),
        stopped=(),
    )


def test_charging_stops_when_the_phase_falls_below_what_a_floor_switching_off_explains(
    f_charge_and_floor_stopping_together: list[PhaseSample],
) -> None:
    detection = detect_ev(
        [CHARGING_BESIDE_A_FLOOR],
        f_charge_and_floor_stopping_together,
        [KITCHEN_SWITCHED_OFF],
        2038.0,
        sheds=(),
        loads=(KITCHEN,),
    )

    assert detection == EvDetection(watches=(), started=(), stopped=(CHARGING_BESIDE_A_FLOOR,))


@pytest.mark.parametrize(
    ('loads', 'sheds'),
    [((replace(KITCHEN, phase=LoadPhase.L1),), ()), ((KITCHEN,), (KITCHEN_SHED,))],
    ids=['floor_on_another_phase', 'floor_the_guard_shed'],
)
def test_a_floor_switching_off_explains_no_drop_it_cannot_cause(
    f_floor_switching_off_beside_a_charge: list[PhaseSample],
    loads: tuple[LoadStatus, ...],
    sheds: tuple[Shed, ...],
) -> None:
    detection = detect_ev(
        [CHARGING_BESIDE_A_FLOOR],
        f_floor_switching_off_beside_a_charge,
        [KITCHEN_SWITCHED_OFF],
        2038.0,
        sheds=sheds,
        loads=loads,
    )

    assert detection == EvDetection(watches=(), started=(), stopped=(CHARGING_BESIDE_A_FLOOR,))


def test_charging_goes_on_through_a_smaller_drop(f_smaller_drop: list[PhaseSample]) -> None:
    assert detect_ev([CHARGING], f_smaller_drop, [], 2003.0, sheds=(), loads=()) == EvDetection(
        watches=(CHARGING,),
        started=(),
        stopped=(),
    )


def test_a_rise_that_drops_back_within_five_minutes_is_forgotten_once_no_load_explains_the_drop(
    f_early_drop: list[PhaseSample],
) -> None:
    assert detect_ev([replace(WATCHED, drop_seen_at=1103.0)], f_early_drop, [], 1138.0, sheds=(), loads=()) == NOTHING


def test_a_rise_a_load_change_seen_later_explains_is_forgotten(f_level_after_the_rise: list[PhaseSample]) -> None:
    changes = [LoadChange(load='kitchen', since=995.0, until=1025.0, up=True)]

    assert detect_ev([WATCHED], f_level_after_the_rise, changes, 1030.0, sheds=(), loads=()) == NOTHING


def test_a_slow_rise_grows_its_step(f_slow_rise: list[PhaseSample]) -> None:
    assert detect_ev([RISING], f_slow_rise, [], 1010.0, sheds=(), loads=()) == EvDetection(
        watches=(replace(RISING, until=1010.0, step=Decimal('16.0')),),
        started=(),
        stopped=(),
    )


def test_watch_waits_for_samples() -> None:
    assert detect_ev([CHARGING], [], [], 2003.0, sheds=(), loads=()) == EvDetection(
        watches=(CHARGING,),
        started=(),
        stopped=(),
    )


def test_a_rise_only_a_load_switching_off_overlaps_is_watched(f_rise_on_l3: list[PhaseSample]) -> None:
    changes = [LoadChange(load='kitchen', since=990.0, until=1020.0, up=False)]

    assert detect_ev([], f_rise_on_l3, changes, RISE_SEEN_AT, sheds=(), loads=()) == EvDetection(
        watches=(WATCHED,),
        started=(),
        stopped=(),
    )


def test_a_rise_across_a_gap_in_the_samples_is_not_watched(f_rise_across_a_gap: list[PhaseSample]) -> None:
    assert detect_ev([], f_rise_across_a_gap, [], RISE_SEEN_AT, sheds=(), loads=()) == NOTHING


def test_a_rise_the_guards_sheds_cut_into_is_watched_at_its_full_step(
    f_rise_the_guard_sheds_into: list[PhaseSample],
) -> None:
    sheds = (replace(BOILER_SHED, at=1001.0), replace(KITCHEN_SHED, at=1002.0))

    assert detect_ev([], f_rise_the_guard_sheds_into, [], 1010.0, sheds=sheds, loads=()) == EvDetection(
        watches=(replace(WATCHED, since=995.0, until=1010.0, before=Decimal('18.0')),),
        started=(),
        stopped=(),
    )


def test_a_rise_waits_while_a_shed_of_the_guard_settles(f_rise_while_a_shed_settles: list[PhaseSample]) -> None:
    detection = detect_ev(
        [],
        f_rise_while_a_shed_settles,
        [],
        1002.0,
        sheds=(replace(BOILER_SHED, at=1001.0),),
        loads=(),
    )

    assert detection == NOTHING


@pytest.mark.parametrize(
    ('f_samples', 'at'),
    [
        (_samples(1980, 2003, {1980: '34.0', 2001: '20.0'}), 2003.0),
        (_samples(1980, 2025, {1980: '34.0', 2001: '20.0'}), 2025.0),
    ],
    ids=['while_the_sheds_are_fresh', 'once_the_sheds_are_older_than_the_rise'],
    indirect=['f_samples'],
)
def test_charging_goes_on_through_the_guards_own_sheds(f_samples: list[PhaseSample], at: float) -> None:
    detection = detect_ev([CHARGING_ON_A_BUSY_PHASE], f_samples, [], at, sheds=(BOILER_SHED, KITCHEN_SHED), loads=())

    assert detection == EvDetection(watches=(CHARGING_ON_A_BUSY_PHASE,), started=(), stopped=())


def test_a_drop_to_the_level_before_the_rise_less_the_guards_sheds_is_held_as_a_stop(
    f_charge_after_the_guards_sheds: list[PhaseSample],
) -> None:
    detection = detect_ev(
        [CHARGING_ON_A_BUSY_PHASE],
        f_charge_after_the_guards_sheds,
        [],
        2043.0,
        sheds=(BOILER_SHED, KITCHEN_SHED),
        loads=(),
    )

    assert detection == EvDetection(
        watches=(replace(CHARGING_ON_A_BUSY_PHASE, drop_seen_at=2043.0),),
        started=(),
        stopped=(),
    )
