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


def test_startup_carries_the_router_uptime_into_the_note(f_use_case: NoteStartupUseCase) -> None:
    output = f_use_case.execute(
        NoteStartupInputDTO(last_activity=LAST_ACTIVITY, monotonic=12.5, router_uptime=ROUTER_UPTIME),
    )

    assert (output.note.router_uptime, output.note.follows_power_cut) == (ROUTER_UPTIME, True)


def test_startup_without_a_readable_uptime_follows_no_power_cut(f_use_case: NoteStartupUseCase) -> None:
    output = f_use_case.execute(NoteStartupInputDTO(last_activity=LAST_ACTIVITY, monotonic=12.5, router_uptime=None))

    assert (output.note.router_uptime, output.note.follows_power_cut) == (None, False)
