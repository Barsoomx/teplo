from datetime import UTC, datetime

import pytest

from apps.notifications.domain.outage import StartupNote
from apps.notifications.services.alert_book import AlertBook
from apps.notifications.usecases.note_startup import NoteStartupInputDTO, NoteStartupOutputDTO, NoteStartupUseCase

LAST_ACTIVITY = datetime(2026, 10, 31, 17, 24, tzinfo=UTC)
NOTE = StartupNote(last_activity=LAST_ACTIVITY, monotonic=12.5)


@pytest.fixture
def f_book() -> AlertBook:
    return AlertBook()


@pytest.fixture
def f_use_case(f_book: AlertBook) -> NoteStartupUseCase:
    return NoteStartupUseCase(book=f_book)


def test_startup_is_noted_for_the_outage_check(f_use_case: NoteStartupUseCase, f_book: AlertBook) -> None:
    output = f_use_case.execute(NoteStartupInputDTO(last_activity=LAST_ACTIVITY, monotonic=12.5))

    assert (output, f_book.startup()) == (NoteStartupOutputDTO(note=NOTE), NOTE)
