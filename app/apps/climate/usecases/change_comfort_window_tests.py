from contextlib import suppress
from datetime import UTC, datetime, time
from functools import partial

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.climate.domain.comfort_windows import RoomWeek, StoredWindow
from apps.climate.domain.errors import ComfortWindowNotFoundError
from apps.climate.domain.zone import ComfortWindow
from apps.climate.factories import ComfortPeriodFactory, RoomClimateFactory
from apps.climate.models import ComfortPeriod
from apps.climate.stubs import ComfortWindowStoreStub
from apps.climate.usecases.change_comfort_window import (
    ChangeComfortWindowInputDTO,
    ChangeComfortWindowOutputDTO,
    ChangeComfortWindowUseCase,
)
from apps.core.domain.errors import SettingsRejectedError
from apps.core.domain.settings import SettingChange, SettingProblem
from apps.core.stubs import SettingsRecorderStub, TransactionStub
from apps.house.factories import RoomFactory
from apps.telemetry.models import JournalEntry

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
) -> ChangeComfortWindowUseCase:
    return ChangeComfortWindowUseCase(windows=f_windows, recorder=f_recorder, transaction=f_transaction)


@pytest.fixture
def f_use_case_after_another_edit_of_its_day_was_saved_meanwhile(
    f_windows: ComfortWindowStoreStub,
    f_recorder: SettingsRecorderStub,
) -> ChangeComfortWindowUseCase:
    return ChangeComfortWindowUseCase(
        windows=f_windows,
        recorder=f_recorder,
        transaction=TransactionStub(meanwhile=partial(f_windows.replace_days, 'kitchen', {5: SATURDAY})),
    )


@pytest.fixture
def f_stored_saturday_morning() -> ComfortPeriod:
    climate = RoomClimateFactory.create(room=RoomFactory.create(slug='kitchen', name='Кухня'))
    ComfortPeriodFactory.create(zone=None, room_climate=climate, weekday=5, start=time(14, 0), end=time(23, 30))
    return ComfortPeriodFactory.create(zone=None, room_climate=climate, weekday=5, start=time(8, 0), end=time(12, 0))


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
        ChangeComfortWindowOutputDTO(room='kitchen', changes=(change,)),
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
        ChangeComfortWindowOutputDTO(room='kitchen', changes=()),
        [],
        [],
    )


def test_unknown_window_raises(f_use_case: ChangeComfortWindowUseCase) -> None:
    with pytest.raises(ComfortWindowNotFoundError):
        f_use_case.execute(ChangeComfortWindowInputDTO(window=99, start=time(9, 0), end=time(13, 0), now=NOW))


@pytest.mark.parametrize(
    ('end', 'outcomes'),
    [(time(13, 0), ['committed']), (time(15, 0), ['rolled_back'])],
    ids=['saved', 'refused'],
)
def test_week_is_read_checked_written_and_journaled_in_one_transaction(
    f_use_case: ChangeComfortWindowUseCase,
    f_transaction: TransactionStub,
    end: time,
    outcomes: list[str],
) -> None:
    with suppress(SettingsRejectedError):
        f_use_case.execute(ChangeComfortWindowInputDTO(window=2, start=time(9, 0), end=end, now=NOW))

    assert f_transaction.outcomes == outcomes


def test_window_whose_day_another_edit_saved_meanwhile_is_not_found_and_nothing_more_is_written(
    f_use_case_after_another_edit_of_its_day_was_saved_meanwhile: ChangeComfortWindowUseCase,
    f_windows: ComfortWindowStoreStub,
    f_recorder: SettingsRecorderStub,
) -> None:
    with pytest.raises(ComfortWindowNotFoundError):
        f_use_case_after_another_edit_of_its_day_was_saved_meanwhile.execute(
            ChangeComfortWindowInputDTO(window=2, start=time(9, 0), end=time(13, 0), now=NOW),
        )

    assert (f_windows.replaced, f_recorder.records) == ([('kitchen', {5: SATURDAY})], [])


@pytest.mark.django_db
def test_room_of_the_window_is_locked_before_its_week_is_read_and_the_change_is_journaled_in_the_same_transaction(
    f_stored_saturday_morning: ComfortPeriod,
) -> None:
    with CaptureQueriesContext(connection) as queries:
        ChangeComfortWindowUseCase.init().execute(
            ChangeComfortWindowInputDTO(
                window=f_stored_saturday_morning.pk,
                start=time(9, 0),
                end=time(13, 0),
                now=NOW,
            ),
        )

    statements = [query['sql'].split(' ', 1)[0] for query in queries.captured_queries]
    assert (
        statements[:3],
        statements[-1],
        list(ComfortPeriod.objects.order_by('start').values_list('weekday', 'start', 'end')),
        list(JournalEntry.objects.values_list('kind', 'subject')),
    ) == (
        ['SAVEPOINT', 'UPDATE', 'SELECT'],
        'RELEASE',
        [(5, time(9, 0), time(13, 0)), (5, time(14, 0), time(23, 30))],
        [('settings_changed', 'kitchen')],
    )
