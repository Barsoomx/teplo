from datetime import UTC, datetime, time

import pytest

from apps.climate.domain.comfort_windows import StoredWindow, ZoneWeek
from apps.climate.domain.errors import ZoneNotFoundError
from apps.climate.domain.zone import ComfortWindow
from apps.climate.stubs import ComfortWindowStoreStub
from apps.climate.usecases.add_comfort_window import (
    AddComfortWindowInputDTO,
    AddComfortWindowOutputDTO,
    AddComfortWindowUseCase,
)
from apps.core.domain.errors import SettingsRejectedError
from apps.core.domain.settings import SettingChange, SettingProblem
from apps.core.stubs import SettingsRecorderStub

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
MONDAY = ComfortWindow(weekday=0, start=time(6, 30), end=time(23, 30))


@pytest.fixture
def f_windows() -> ComfortWindowStoreStub:
    return ComfortWindowStoreStub(
        weeks=[
            ZoneWeek(
                slug='kitchen',
                name='Кухня',
                windows=(StoredWindow(id=1, weekday=0, start=time(6, 30), end=time(23, 30)),),
            ),
        ],
    )


@pytest.fixture
def f_recorder() -> SettingsRecorderStub:
    return SettingsRecorderStub(error=None)


@pytest.fixture
def f_use_case(f_windows: ComfortWindowStoreStub, f_recorder: SettingsRecorderStub) -> AddComfortWindowUseCase:
    return AddComfortWindowUseCase(windows=f_windows, recorder=f_recorder)


def test_added_window_joins_its_day_and_the_day_is_journaled(
    f_use_case: AddComfortWindowUseCase,
    f_windows: ComfortWindowStoreStub,
    f_recorder: SettingsRecorderStub,
) -> None:
    output = f_use_case.execute(
        AddComfortWindowInputDTO(zone='kitchen', weekday=0, start=time(5, 0), end=time(6, 0), now=NOW),
    )

    change = SettingChange(
        target='schedule',
        subject='kitchen',
        name='Кухня',
        field='monday',
        old=('06:30-23:30',),
        new=('05:00-06:00', '06:30-23:30'),
    )
    assert (output, f_windows.replaced, f_recorder.records) == (
        AddComfortWindowOutputDTO(zone='kitchen', changes=(change,)),
        [('kitchen', {0: (ComfortWindow(weekday=0, start=time(5, 0), end=time(6, 0)), MONDAY)})],
        [((change,), NOW)],
    )


def test_window_added_to_a_day_without_comfort_gives_it_one(
    f_use_case: AddComfortWindowUseCase,
    f_recorder: SettingsRecorderStub,
) -> None:
    f_use_case.execute(AddComfortWindowInputDTO(zone='kitchen', weekday=6, start=time(9, 0), end=time(22, 0), now=NOW))

    assert f_recorder.records == [
        (
            (
                SettingChange(
                    target='schedule',
                    subject='kitchen',
                    name='Кухня',
                    field='sunday',
                    old=(),
                    new=('09:00-22:00',),
                ),
            ),
            NOW,
        ),
    ]


@pytest.mark.parametrize(
    ('start', 'end', 'problems'),
    [
        (time(5, 10), time(6, 0), (SettingProblem(field='start', code='off_grid'),)),
        (time(7, 0), time(6, 0), (SettingProblem(field='end', code='not_after_start'),)),
        (time(23, 0), time(23, 45), (SettingProblem(field='', code='overlap'),)),
    ],
    ids=['off_the_grid', 'backwards', 'overlapping'],
)
def test_window_off_the_grid_backwards_or_overlapping_is_refused(
    f_use_case: AddComfortWindowUseCase,
    f_windows: ComfortWindowStoreStub,
    f_recorder: SettingsRecorderStub,
    start: time,
    end: time,
    problems: tuple[SettingProblem, ...],
) -> None:
    with pytest.raises(SettingsRejectedError) as raised:
        f_use_case.execute(AddComfortWindowInputDTO(zone='kitchen', weekday=0, start=start, end=end, now=NOW))

    assert (raised.value.problems, f_windows.replaced, f_recorder.records) == (problems, [], [])


def test_window_for_an_unknown_zone_raises(f_use_case: AddComfortWindowUseCase) -> None:
    with pytest.raises(ZoneNotFoundError):
        f_use_case.execute(AddComfortWindowInputDTO(zone='attic', weekday=0, start=time(5, 0), end=time(6, 0), now=NOW))
