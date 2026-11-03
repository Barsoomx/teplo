from datetime import UTC, datetime, time

import pytest

from apps.climate.domain.comfort_windows import StoredWindow, ZoneWeek
from apps.climate.domain.errors import ComfortWindowNotFoundError
from apps.climate.domain.zone import ComfortWindow
from apps.climate.stubs import ComfortWindowStoreStub
from apps.climate.usecases.change_comfort_window import (
    ChangeComfortWindowInputDTO,
    ChangeComfortWindowOutputDTO,
    ChangeComfortWindowUseCase,
)
from apps.core.domain.errors import SettingsRejectedError
from apps.core.domain.settings import SettingChange, SettingProblem
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
def f_use_case(f_windows: ComfortWindowStoreStub, f_recorder: SettingsRecorderStub) -> ChangeComfortWindowUseCase:
    return ChangeComfortWindowUseCase(windows=f_windows, recorder=f_recorder)


def test_changed_window_takes_its_new_times_beside_the_rest_of_its_day(
    f_use_case: ChangeComfortWindowUseCase,
    f_windows: ComfortWindowStoreStub,
    f_recorder: SettingsRecorderStub,
) -> None:
    output = f_use_case.execute(ChangeComfortWindowInputDTO(window=2, start=time(9, 0), end=time(13, 0), now=NOW))

    change = SettingChange(
        target='schedule',
        subject='kitchen',
        name='Кухня',
        field='saturday',
        old=('08:00-12:00', '14:00-23:30'),
        new=('09:00-13:00', '14:00-23:30'),
    )
    assert (output, f_windows.replaced, f_recorder.records) == (
        ChangeComfortWindowOutputDTO(zone='kitchen', changes=(change,)),
        [
            (
                'kitchen',
                {
                    5: (
                        ComfortWindow(weekday=5, start=time(9, 0), end=time(13, 0)),
                        ComfortWindow(weekday=5, start=time(14, 0), end=time(23, 30)),
                    ),
                },
            ),
        ],
        [((change,), NOW)],
    )


def test_window_stretched_over_its_neighbour_is_refused(
    f_use_case: ChangeComfortWindowUseCase,
    f_windows: ComfortWindowStoreStub,
) -> None:
    with pytest.raises(SettingsRejectedError) as raised:
        f_use_case.execute(ChangeComfortWindowInputDTO(window=2, start=time(8, 0), end=time(15, 0), now=NOW))

    assert (raised.value.problems, f_windows.replaced) == ((SettingProblem(field='', code='overlap'),), [])


def test_window_kept_as_it_was_writes_and_journals_nothing(
    f_use_case: ChangeComfortWindowUseCase,
    f_windows: ComfortWindowStoreStub,
    f_recorder: SettingsRecorderStub,
) -> None:
    output = f_use_case.execute(ChangeComfortWindowInputDTO(window=1, start=time(6, 30), end=time(23, 30), now=NOW))

    assert (output, f_windows.replaced, f_recorder.records) == (
        ChangeComfortWindowOutputDTO(zone='kitchen', changes=()),
        [],
        [],
    )


def test_unknown_window_raises(f_use_case: ChangeComfortWindowUseCase) -> None:
    with pytest.raises(ComfortWindowNotFoundError):
        f_use_case.execute(ChangeComfortWindowInputDTO(window=99, start=time(9, 0), end=time(13, 0), now=NOW))
