from datetime import UTC, datetime, time

import pytest

from apps.climate.domain.comfort_windows import StoredWindow, ZoneWeek
from apps.climate.domain.errors import ZoneNotFoundError
from apps.climate.domain.zone import ComfortWindow
from apps.climate.stubs import ComfortWindowStoreStub
from apps.climate.usecases.copy_comfort_week import (
    CopyComfortWeekInputDTO,
    CopyComfortWeekOutputDTO,
    CopyComfortWeekUseCase,
)
from apps.core.domain.settings import SettingChange
from apps.core.stubs import SettingsRecorderStub

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)


def _hallway_change(field: str, old: tuple[str, ...], new: tuple[str, ...]) -> SettingChange:
    return SettingChange(target='schedule', subject='hallway', name='Прихожая', field=field, old=old, new=new)


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
                ),
            ),
            ZoneWeek(
                slug='hallway',
                name='Прихожая',
                windows=(
                    StoredWindow(id=10, weekday=0, start=time(7, 0), end=time(23, 0)),
                    StoredWindow(id=11, weekday=6, start=time(9, 0), end=time(22, 0)),
                ),
            ),
            ZoneWeek(
                slug='bathroom-2f',
                name='Санузел 2 этаж',
                windows=(
                    StoredWindow(id=20, weekday=0, start=time(6, 30), end=time(23, 30)),
                    StoredWindow(id=21, weekday=5, start=time(8, 0), end=time(12, 0)),
                ),
            ),
        ],
    )


@pytest.fixture
def f_recorder() -> SettingsRecorderStub:
    return SettingsRecorderStub(error=None)


@pytest.fixture
def f_use_case(f_windows: ComfortWindowStoreStub, f_recorder: SettingsRecorderStub) -> CopyComfortWeekUseCase:
    return CopyComfortWeekUseCase(windows=f_windows, recorder=f_recorder)


def test_week_copied_to_another_zone_replaces_the_days_that_differ(
    f_use_case: CopyComfortWeekUseCase,
    f_windows: ComfortWindowStoreStub,
    f_recorder: SettingsRecorderStub,
) -> None:
    output = f_use_case.execute(CopyComfortWeekInputDTO(zone='kitchen', to_zones=('hallway',), now=NOW))

    changes = (
        _hallway_change('monday', ('07:00-23:00',), ('06:30-23:30',)),
        _hallway_change('saturday', (), ('08:00-12:00',)),
        _hallway_change('sunday', ('09:00-22:00',), ()),
    )
    assert (output, f_windows.replaced, f_recorder.records) == (
        CopyComfortWeekOutputDTO(zones=('hallway',), changes=changes),
        [
            (
                'hallway',
                {
                    0: (ComfortWindow(weekday=0, start=time(6, 30), end=time(23, 30)),),
                    5: (ComfortWindow(weekday=5, start=time(8, 0), end=time(12, 0)),),
                    6: (),
                },
            ),
        ],
        [(changes, NOW)],
    )


def test_zone_with_the_same_week_and_the_source_zone_are_left_as_they_are(
    f_use_case: CopyComfortWeekUseCase,
    f_windows: ComfortWindowStoreStub,
    f_recorder: SettingsRecorderStub,
) -> None:
    output = f_use_case.execute(CopyComfortWeekInputDTO(zone='kitchen', to_zones=('bathroom-2f', 'kitchen'), now=NOW))

    assert (output, f_windows.replaced, f_recorder.records) == (
        CopyComfortWeekOutputDTO(zones=('bathroom-2f',), changes=()),
        [],
        [],
    )


def test_week_copied_to_an_unknown_zone_raises_and_writes_nothing(
    f_use_case: CopyComfortWeekUseCase,
    f_windows: ComfortWindowStoreStub,
) -> None:
    with pytest.raises(ZoneNotFoundError):
        f_use_case.execute(CopyComfortWeekInputDTO(zone='kitchen', to_zones=('hallway', 'attic'), now=NOW))

    assert f_windows.replaced == []
