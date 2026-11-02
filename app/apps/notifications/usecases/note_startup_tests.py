from datetime import UTC, datetime, timedelta

import pytest

from apps.notifications.domain.outage import StartupNote
from apps.notifications.services.alert_book import AlertBook
from apps.notifications.usecases.note_startup import NoteStartupInputDTO, NoteStartupOutputDTO, NoteStartupUseCase

LAST_ACTIVITY = datetime(2026, 10, 31, 17, 24, tzinfo=UTC)
ROUTER_UPTIME = timedelta(minutes=2, seconds=31)
NOTE = StartupNote(last_activity=LAST_ACTIVITY, monotonic=12.5, router_uptime=ROUTER_UPTIME)


@pytest.fixture
def f_book() -> AlertBook:
    return AlertBook()


@pytest.fixture
def f_use_case(f_book: AlertBook) -> NoteStartupUseCase:
    return NoteStartupUseCase(book=f_book)


def test_startup_is_noted_for_the_outage_check(f_use_case: NoteStartupUseCase, f_book: AlertBook) -> None:
    output = f_use_case.execute(
        NoteStartupInputDTO(last_activity=LAST_ACTIVITY, monotonic=12.5, router_uptime=ROUTER_UPTIME),
    )

    assert (output, f_book.startup()) == (NoteStartupOutputDTO(note=NOTE), NOTE)


@pytest.mark.parametrize('router_uptime', [ROUTER_UPTIME, None], ids=['uptime_read', 'uptime_unreadable'])
def test_startup_carries_the_router_uptime_into_the_note(
    f_use_case: NoteStartupUseCase,
    router_uptime: timedelta | None,
) -> None:
    output = f_use_case.execute(
        NoteStartupInputDTO(last_activity=LAST_ACTIVITY, monotonic=12.5, router_uptime=router_uptime),
    )

    assert output.note == StartupNote(last_activity=LAST_ACTIVITY, monotonic=12.5, router_uptime=router_uptime)
