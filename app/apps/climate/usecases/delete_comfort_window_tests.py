from contextlib import suppress
from datetime import UTC, datetime, time
from functools import partial

import pytest

from apps.climate.domain.comfort_windows import RoomWeek, StoredWindow
from apps.climate.domain.errors import ComfortWindowNotFoundError
from apps.climate.domain.zone import ComfortWindow
from apps.climate.stubs import ComfortWindowStoreStub
from apps.climate.usecases.delete_comfort_window import (
    DeleteComfortWindowInputDTO,
    DeleteComfortWindowOutputDTO,
    DeleteComfortWindowUseCase,
)
from apps.core.domain.settings import SettingChange
from apps.core.stubs import SettingsRecorderStub, TransactionStub

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
SATURDAY = (
    ComfortWindow(weekday=5, start=time(8, 0), end=time(12, 0)),
    ComfortWindow(weekday=5, start=time(14, 0), end=time(23, 30)),
)


@pytest.fixture
def f_windows() -> ComfortWindowStoreStub:
    return ComfortWindowStoreStub(
        weeks=[
            RoomWeek(
                slug='kitchen',
                name='Кухня',
                windows=(
                    StoredWindow(id=1, weekday=0, start=time(6, 30), end=time(23, 30)),
                    StoredWindow(id=2, weekday=5, start=time(8, 0), end=time(12, 0)),
                    StoredWindow(id=3, weekday=5, start=time(14, 0), end=time(23, 30)),
                ),
            ),
        ],
    )


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
) -> DeleteComfortWindowUseCase:
    return DeleteComfortWindowUseCase(windows=f_windows, recorder=f_recorder, transaction=f_transaction)


@pytest.fixture
def f_use_case_after_another_edit_of_its_day_was_saved_meanwhile(
    f_windows: ComfortWindowStoreStub,
    f_recorder: SettingsRecorderStub,
) -> DeleteComfortWindowUseCase:
    return DeleteComfortWindowUseCase(
        windows=f_windows,
        recorder=f_recorder,
        transaction=TransactionStub(meanwhile=partial(f_windows.replace_days, 'kitchen', {5: SATURDAY})),
    )


def test_deleted_window_leaves_the_rest_of_its_day(
    f_use_case: DeleteComfortWindowUseCase,
    f_windows: ComfortWindowStoreStub,
    f_recorder: SettingsRecorderStub,
) -> None:
    output = f_use_case.execute(DeleteComfortWindowInputDTO(window=3, now=NOW))

    change = SettingChange(
        target='schedule',
        subject='kitchen',
        name='Кухня',
        field='saturday',
        old=('08:00-12:00', '14:00-23:30'),
        new=('08:00-12:00',),
    )
    assert (output, f_windows.replaced, f_recorder.records) == (
        DeleteComfortWindowOutputDTO(room='kitchen', changes=(change,)),
        [('kitchen', {5: (ComfortWindow(weekday=5, start=time(8, 0), end=time(12, 0)),)})],
        [((change,), NOW)],
    )


def test_deleting_the_last_window_of_a_day_leaves_it_night_all_day(
    f_use_case: DeleteComfortWindowUseCase,
    f_windows: ComfortWindowStoreStub,
    f_recorder: SettingsRecorderStub,
) -> None:
    f_use_case.execute(DeleteComfortWindowInputDTO(window=1, now=NOW))

    assert (f_windows.replaced, f_recorder.records) == (
        [('kitchen', {0: ()})],
        [
            (
                (
                    SettingChange(
                        target='schedule',
                        subject='kitchen',
                        name='Кухня',
                        field='monday',
                        old=('06:30-23:30',),
                        new=(),
                    ),
                ),
                NOW,
            ),
        ],
    )


def test_unknown_window_raises(f_use_case: DeleteComfortWindowUseCase) -> None:
    with pytest.raises(ComfortWindowNotFoundError):
        f_use_case.execute(DeleteComfortWindowInputDTO(window=99, now=NOW))


@pytest.mark.parametrize(
    ('window', 'outcomes'),
    [(3, ['committed']), (99, ['rolled_back'])],
    ids=['deleted', 'unknown_window'],
)
def test_week_is_read_written_and_journaled_in_one_transaction(
    f_use_case: DeleteComfortWindowUseCase,
    f_transaction: TransactionStub,
    window: int,
    outcomes: list[str],
) -> None:
    with suppress(ComfortWindowNotFoundError):
        f_use_case.execute(DeleteComfortWindowInputDTO(window=window, now=NOW))

    assert f_transaction.outcomes == outcomes


def test_window_whose_day_another_edit_saved_meanwhile_is_not_found_and_nothing_more_is_written(
    f_use_case_after_another_edit_of_its_day_was_saved_meanwhile: DeleteComfortWindowUseCase,
    f_windows: ComfortWindowStoreStub,
    f_recorder: SettingsRecorderStub,
) -> None:
    with pytest.raises(ComfortWindowNotFoundError):
        f_use_case_after_another_edit_of_its_day_was_saved_meanwhile.execute(
            DeleteComfortWindowInputDTO(window=3, now=NOW),
        )

    assert (f_windows.replaced, f_recorder.records) == ([('kitchen', {5: SATURDAY})], [])
