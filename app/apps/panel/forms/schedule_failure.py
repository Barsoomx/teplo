from dataclasses import dataclass

from django import forms


@dataclass(frozen=True, kw_only=True)
class ScheduleFailure:
    action: str
    messages: tuple[str, ...]
    weekday: int | None = None
    window: int | None = None
    room: str = ''
    start: str = ''
    end: str = ''
    chosen: tuple[str, ...] = ()


def form_messages(form: forms.BaseForm) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(message) for messages in form.errors.values() for message in messages))
