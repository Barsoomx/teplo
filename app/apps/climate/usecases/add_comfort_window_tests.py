from contextlib import suppress
from datetime import UTC, datetime, time
from functools import partial

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.climate.domain.comfort_windows import StoredWindow, ZoneWeek
from apps.climate.domain.errors import ZoneNotFoundError
from apps.climate.domain.zone import ComfortWindow
from apps.climate.factories import ComfortPeriodFactory, ZoneFactory
from apps.climate.models import ComfortPeriod, Zone
from apps.climate.stubs import ComfortWindowStoreStub
from apps.climate.usecases.add_comfort_window import (
    AddComfortWindowInputDTO,
    AddComfortWindowOutputDTO,
    AddComfortWindowUseCase,
)
from apps.core.domain.errors import SettingsRejectedError
from apps.core.domain.settings import SettingChange, SettingProblem
from apps.core.stubs import SettingsRecorderStub, TransactionStub
from apps.telemetry.models import JournalEntry

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
MONDAY = ComfortWindow(weekday=0, start=time(6, 30), end=time(23, 30))
EARLY_MONDAY = ComfortWindow(weekday=0, start=time(5, 0), end=time(6, 0))


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
def f_transaction() -> TransactionStub:
    return TransactionStub(meanwhile=None)


@pytest.fixture
def f_use_case(
    f_windows: ComfortWindowStoreStub,
    f_recorder: SettingsRecorderStub,
    f_transaction: TransactionStub,
) -> AddComfortWindowUseCase:
    return AddComfortWindowUseCase(windows=f_windows, recorder=f_recorder, transaction=f_transaction)


@pytest.fixture
def f_use_case_after_another_window_was_added_meanwhile(
    f_windows: ComfortWindowStoreStub,
    f_recorder: SettingsRecorderStub,
) -> AddComfortWindowUseCase:
    return AddComfortWindowUseCase(
        windows=f_windows,
        recorder=f_recorder,
        transaction=TransactionStub(meanwhile=partial(f_windows.replace_days, 'kitchen', {0: (EARLY_MONDAY, MONDAY)})),
    )


@pytest.fixture
def f_stored_kitchen() -> Zone:
    zone = ZoneFactory.create(slug='kitchen', name='Кухня')
    ComfortPeriodFactory.create(zone=zone, weekday=0, start=time(6, 30), end=time(23, 30))
    return zone


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


@pytest.mark.parametrize(
    ('end', 'outcomes'),
    [(time(6, 0), ['committed']), (time(4, 0), ['rolled_back'])],
    ids=['saved', 'refused'],
)
def test_week_is_read_checked_written_and_journaled_in_one_transaction(
    f_use_case: AddComfortWindowUseCase,
    f_transaction: TransactionStub,
    end: time,
    outcomes: list[str],
) -> None:
    with suppress(SettingsRejectedError):
        f_use_case.execute(AddComfortWindowInputDTO(zone='kitchen', weekday=0, start=time(5, 0), end=end, now=NOW))

    assert f_transaction.outcomes == outcomes


def test_window_added_after_another_add_to_its_day_keeps_the_window_that_add_stored(
    f_use_case_after_another_window_was_added_meanwhile: AddComfortWindowUseCase,
    f_windows: ComfortWindowStoreStub,
) -> None:
    output = f_use_case_after_another_window_was_added_meanwhile.execute(
        AddComfortWindowInputDTO(zone='kitchen', weekday=0, start=time(4, 0), end=time(4, 45), now=NOW),
    )

    added = ComfortWindow(weekday=0, start=time(4, 0), end=time(4, 45))
    assert (output.changes, f_windows.replaced[-1]) == (
        (
            SettingChange(
                target='schedule',
                subject='kitchen',
                name='Кухня',
                field='monday',
                old=('05:00-06:00', '06:30-23:30'),
                new=('04:00-04:45', '05:00-06:00', '06:30-23:30'),
            ),
        ),
        ('kitchen', {0: (added, EARLY_MONDAY, MONDAY)}),
    )


@pytest.mark.django_db
def test_zone_is_locked_before_its_week_is_read_and_the_added_window_is_journaled_in_the_same_transaction(
    f_stored_kitchen: Zone,
) -> None:
    with CaptureQueriesContext(connection) as queries:
        AddComfortWindowUseCase.init().execute(
            AddComfortWindowInputDTO(zone='kitchen', weekday=0, start=time(5, 0), end=time(6, 0), now=NOW),
        )

    statements = [query['sql'].split(' ', 1)[0] for query in queries.captured_queries]
    assert (
        statements[:3],
        statements[-1],
        list(ComfortPeriod.objects.filter(zone=f_stored_kitchen).order_by('start').values_list('start', 'end')),
        list(JournalEntry.objects.values_list('kind', 'subject')),
    ) == (
        ['SAVEPOINT', 'UPDATE', 'SELECT'],
        'RELEASE',
        [(time(5, 0), time(6, 0)), (time(6, 30), time(23, 30))],
        [('settings_changed', 'kitchen')],
    )
