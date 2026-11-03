from collections.abc import Callable, Sequence
from datetime import datetime

from django.db import transaction

from apps.core.domain.settings import SettingChange
from apps.telemetry.domain.ports import JournalWriter
from apps.telemetry.repositories.journal import JournalRepository

SETTINGS_CHANGED = 'settings_changed'


class SettingsJournal:
    def __init__(self, journal: JournalWriter) -> None:
        self._journal = journal

    @classmethod
    def init(cls) -> 'SettingsJournal':
        return cls(journal=JournalRepository())

    def record(self, changes: Sequence[SettingChange], at: datetime, write: Callable[[], None]) -> None:
        with transaction.atomic():
            write()
            for change in changes:
                self._journal.add(
                    kind=SETTINGS_CHANGED,
                    subject=change.subject,
                    payload=change.payload(),
                    created_at=at,
                )
