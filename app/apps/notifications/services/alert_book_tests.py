from datetime import UTC, datetime

import pytest

from apps.notifications.domain.alerts import AlertKey, AlertKind
from apps.notifications.domain.outage import StartupNote
from apps.notifications.domain.policy import AlertTrack
from apps.notifications.services.alert_book import AlertBook

NOW = datetime(2026, 10, 31, 17, 40, tzinfo=UTC)
KEY = AlertKey(kind=AlertKind.DEVICE_LOST, subject='thermostat-kitchen')
TRACK = AlertTrack(began_at=4.0, noticed_at=10.0, raised=False)
NOTE = StartupNote(last_activity=NOW, monotonic=5.0)


@pytest.fixture
def f_book() -> AlertBook:
    book = AlertBook()
    book.replace_tracks({KEY: TRACK})
    book.note_startup(NOTE)
    return book


def test_tracks_are_copied_out(f_book: AlertBook) -> None:
    tracks = f_book.tracks()
    tracks.clear()

    assert f_book.tracks() == {KEY: TRACK}


def test_startup_note_is_kept_until_forgotten(f_book: AlertBook) -> None:
    kept = f_book.startup()
    f_book.forget_startup()

    assert (kept, f_book.startup()) == (NOTE, None)
