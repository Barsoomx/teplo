from datetime import datetime

from apps.core.services.secrets import TelegramSecret
from apps.notifications.domain.bot import BotUpdate
from apps.notifications.domain.commands import OwnerCommand
from apps.telemetry.repositories.journal import JournalRepository

OWNER_COMMAND = 'owner_command'
TEXT_LIMIT = 200


class JournalCommandLedger:
    def __init__(self, journal: JournalRepository, bot: str) -> None:
        self._journal = journal
        self._bot = bot

    @classmethod
    def init(cls, secret: TelegramSecret) -> 'JournalCommandLedger':
        return cls(journal=JournalRepository(), bot=secret.token.partition(':')[0])

    def last_update_id(self) -> int | None:
        payload = self._journal.newest_payload(OWNER_COMMAND, {'bot': self._bot})
        update_id = None if payload is None else payload.get('update_id')
        if isinstance(update_id, bool) or not isinstance(update_id, int):
            return None

        return update_id

    def record(self, update: BotUpdate, command: OwnerCommand, at: datetime) -> None:
        room = '' if command.room is None else command.room.slug
        self._journal.add(
            kind=OWNER_COMMAND,
            subject=room,
            payload={
                'update_id': update.update_id,
                'bot': self._bot,
                'text': (update.text or '')[:TEXT_LIMIT],
                'command': command.kind.value,
                'room': room,
            },
            created_at=at,
        )
