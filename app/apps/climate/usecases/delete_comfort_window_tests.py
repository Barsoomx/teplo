from datetime import UTC, datetime, time

import pytest

from apps.climate.domain.comfort_windows import StoredWindow, ZoneWeek
from apps.climate.domain.errors import ComfortWindowNotFoundError
from apps.climate.domain.zone import ComfortWindow
from apps.climate.stubs import ComfortWindowStoreStub
from apps.climate.usecases.delete_comfort_window import (
    DeleteComfortWindowInputDTO,
    DeleteComfortWindowOutputDTO,
    DeleteComfortWindowUseCase,
)
from apps.core.domain.settings import SettingChange
from apps.core.stubs import SettingsRecorderStub

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)


@pytest.fixture
def f_windows() -> ComfortWindowStoreStub:
    return ComfortWindowStoreStub(
        weeks=[
            ZoneWeek(
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
def f_use_case(f_windows: ComfortWindowStoreStub, f_recorder: SettingsRecorderStub) -> DeleteComfortWindowUseCase:
    return DeleteComfortWindowUseCase(windows=f_windows, recorder=f_recorder)


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
        DeleteComfortWindowOutputDTO(zone='kitchen', changes=(change,)),
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
