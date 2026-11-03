from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Protocol

from apps.core.domain.settings import SettingChange


class SettingsRecorder(Protocol):
    def record(self, changes: Sequence[SettingChange], at: datetime, write: Callable[[], None]) -> None: ...
