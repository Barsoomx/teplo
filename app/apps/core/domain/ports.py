from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Protocol

from apps.core.domain.settings import SettingChange


class SettingsRecorder(Protocol):
    def record(self, changes: Sequence[SettingChange], at: datetime, write: Callable[[], None]) -> None: ...


class Transaction(Protocol):
    def __call__(self) -> AbstractContextManager[None]: ...


class Executable[TInput, TOutput](Protocol):
    def execute(self, input_dto: TInput) -> TOutput: ...
