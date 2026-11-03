from contextlib import suppress
from datetime import UTC, datetime, time
from functools import partial

import pytest

from apps.climate.domain.comfort_windows import StoredWindow, ZoneWeek
from apps.climate.domain.errors import ZoneNotFoundError
from apps.climate.domain.zone import ComfortWindow
from apps.climate.stubs import ComfortWindowStoreStub
from apps.climate.usecases.copy_comfort_day import (
    CopyComfortDayInputDTO,
    CopyComfortDayOutputDTO,
    CopyComfortDayUseCase,
)
from apps.core.domain.errors import SettingsRejectedError
from apps.core.domain.settings import SettingChange, SettingProblem
from apps.core.stubs import SettingsRecorderStub, TransactionStub

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
MONDAY = ComfortWindow(weekday=0, start=time(6, 30), end=time(23, 30))
EARLY_MONDAY = ComfortWindow(weekday=0, start=time(5, 0), end=time(6, 0))
KITCHEN_WEEK = ZoneWeek(
    slug='kitchen',
    name='Кухня',
    windows=(
        StoredWindow(id=1, weekday=0, start=time(6, 30), end=time(23, 30)),
        StoredWindow(id=2, weekday=5, start=time(8, 0), end=time(12, 0)),
        StoredWindow(id=3, weekday=5, start=time(14, 0), end=time(23, 30)),
    ),
)


def _schedule_change(field: str, old: tuple[str, ...], new: tuple[str, ...]) -> SettingChange:
    return SettingChange(target='schedule', subject='kitchen', name='Кухня', field=field, old=old, new=new)


@pytest.fixture
def f_windows() -> ComfortWindowStoreStub:
    return ComfortWindowStoreStub(weeks=[KITCHEN_WEEK])


@pytest.fixture
def f_recorder() -> SettingsRecorderStub:
    return SettingsRecorderStub(error=None)


@pytest.fixture
def f_transaction() -> TransactionStub:
    return TransactionStub(meanwhile=None)


@pytest.fixture
def f_use_case(
    f_windows: ComfortWindowStoreStub,
    f_recorder: SettingsRecorderStub,
    f_transaction: TransactionStub,
) -> CopyComfortDayUseCase:
    return CopyComfortDayUseCase(windows=f_windows, recorder=f_recorder, transaction=f_transaction)


@pytest.fixture
def f_off_grid_use_case() -> CopyComfortDayUseCase:
    week = ZoneWeek(
        slug='kitchen',
        name='Кухня',
        windows=(StoredWindow(id=1, weekday=0, start=time(6, 30), end=time(23, 59)),),
    )
    return CopyComfortDayUseCase(
        windows=ComfortWindowStoreStub(weeks=[week]),
        recorder=SettingsRecorderStub(error=None),
        transaction=TransactionStub(meanwhile=None),
    )


@pytest.fixture
def f_use_case_after_a_window_was_added_to_the_day_meanwhile(
    f_windows: ComfortWindowStoreStub,
    f_recorder: SettingsRecorderStub,
) -> CopyComfortDayUseCase:
    return CopyComfortDayUseCase(
        windows=f_windows,
        recorder=f_recorder,
        transaction=TransactionStub(meanwhile=partial(f_windows.replace_days, 'kitchen', {0: (EARLY_MONDAY, MONDAY)})),
    )


def test_day_copied_to_other_days_replaces_their_windows(
    f_use_case: CopyComfortDayUseCase,
    f_windows: ComfortWindowStoreStub,
    f_recorder: SettingsRecorderStub,
) -> None:
    output = f_use_case.execute(CopyComfortDayInputDTO(zone='kitchen', weekday=0, to_weekdays=(5, 1), now=NOW))

    changes = (
        _schedule_change('tuesday', (), ('06:30-23:30',)),
        _schedule_change('saturday', ('08:00-12:00', '14:00-23:30'), ('06:30-23:30',)),
    )
    assert (output, f_windows.replaced, f_recorder.records) == (
        CopyComfortDayOutputDTO(zone='kitchen', changes=changes),
        [
            (
                'kitchen',
                {
                    1: (ComfortWindow(weekday=1, start=time(6, 30), end=time(23, 30)),),
                    5: (ComfortWindow(weekday=5, start=time(6, 30), end=time(23, 30)),),
                },
            ),
        ],
        [(changes, NOW)],
    )


def test_day_without_comfort_copied_clears_the_other_days(
    f_use_case: CopyComfortDayUseCase,
    f_windows: ComfortWindowStoreStub,
) -> None:
    f_use_case.execute(CopyComfortDayInputDTO(zone='kitchen', weekday=2, to_weekdays=(0, 3), now=NOW))

    assert f_windows.replaced == [('kitchen', {0: ()})]


@pytest.mark.parametrize('to_weekdays', [(0,), ()], ids=['onto_itself', 'nowhere'])
def test_day_copied_onto_itself_or_nowhere_changes_nothing(
    f_use_case: CopyComfortDayUseCase,
    f_windows: ComfortWindowStoreStub,
    f_recorder: SettingsRecorderStub,
    to_weekdays: tuple[int, ...],
) -> None:
    output = f_use_case.execute(CopyComfortDayInputDTO(zone='kitchen', weekday=0, to_weekdays=to_weekdays, now=NOW))

    assert (output, f_windows.replaced, f_recorder.records) == (
        CopyComfortDayOutputDTO(zone='kitchen', changes=()),
        [],
        [],
    )


def test_day_with_a_window_off_the_grid_is_not_copied(f_off_grid_use_case: CopyComfortDayUseCase) -> None:
    with pytest.raises(SettingsRejectedError) as raised:
        f_off_grid_use_case.execute(CopyComfortDayInputDTO(zone='kitchen', weekday=0, to_weekdays=(1,), now=NOW))

    assert raised.value.problems == (SettingProblem(field='end', code='off_grid'),)


def test_day_of_an_unknown_zone_raises(f_use_case: CopyComfortDayUseCase) -> None:
    with pytest.raises(ZoneNotFoundError):
        f_use_case.execute(CopyComfortDayInputDTO(zone='attic', weekday=0, to_weekdays=(1,), now=NOW))


@pytest.mark.parametrize(
    ('weekday', 'outcomes'),
    [(1, ['committed']), (7, ['rolled_back'])],
    ids=['copied', 'refused'],
)
def test_week_is_read_checked_written_and_journaled_in_one_transaction(
    f_use_case: CopyComfortDayUseCase,
    f_transaction: TransactionStub,
    weekday: int,
    outcomes: list[str],
) -> None:
    with suppress(SettingsRejectedError):
        f_use_case.execute(CopyComfortDayInputDTO(zone='kitchen', weekday=0, to_weekdays=(weekday,), now=NOW))

    assert f_transaction.outcomes == outcomes


def test_day_copied_after_a_window_was_added_to_it_copies_that_window_too(
    f_use_case_after_a_window_was_added_to_the_day_meanwhile: CopyComfortDayUseCase,
    f_windows: ComfortWindowStoreStub,
) -> None:
    f_use_case_after_a_window_was_added_to_the_day_meanwhile.execute(
        CopyComfortDayInputDTO(zone='kitchen', weekday=0, to_weekdays=(1,), now=NOW),
    )

    assert f_windows.replaced[-1] == (
        'kitchen',
        {
            1: (
                ComfortWindow(weekday=1, start=time(5, 0), end=time(6, 0)),
                ComfortWindow(weekday=1, start=time(6, 30), end=time(23, 30)),
            ),
        },
    )
