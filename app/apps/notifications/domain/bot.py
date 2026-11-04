from dataclasses import dataclass
from datetime import datetime, timedelta

STALE_AFTER = timedelta(minutes=10)


@dataclass(frozen=True, kw_only=True)
class PollFailure:
    error_code: str
    detail: str


@dataclass(frozen=True, kw_only=True)
class BotUpdate:
    update_id: int
    chat_id: str
    text: str | None
    sent_at: datetime | None
    edited: bool


def accepted(update: BotUpdate, chat_id: str, now: datetime) -> bool:
    if update.edited or not (update.text or '').strip() or update.chat_id != chat_id:
        return False

    return update.sent_at is not None and update.sent_at >= now - STALE_AFTER
