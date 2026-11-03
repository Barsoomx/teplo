from datetime import UTC, datetime, timedelta

import pytest

from apps.core.services.secrets import TelegramSecret
from apps.notifications.domain.bot import BotUpdate
from apps.notifications.domain.commands import CommandKind, CommandRoom, OwnerCommand
from apps.notifications.services.command_ledger import JournalCommandLedger
from apps.telemetry.factories import JournalEntryFactory
from apps.telemetry.models import JournalEntry
from apps.telemetry.repositories.journal import JournalRepository

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 4, 13, 40, tzinfo=UTC)
SECRET = TelegramSecret(token='123456:telegram-token', chat_id='-1001234', proxy=None, commands=True)
OPEN_SPACE = CommandRoom(slug='open-space', name='Гостиная, кухня и прихожая', level=1, stems=('гостин',))


@pytest.fixture
def f_ledger() -> JournalCommandLedger:
    return JournalCommandLedger.init(SECRET)


@pytest.fixture
def journaled_commands() -> None:
    for update_id, bot in ((40, '123456'), (42, '123456'), (41, '123456'), (57, '654321')):
        JournalEntryFactory.create(
            kind='owner_command',
            subject='',
            payload={'update_id': update_id, 'bot': bot, 'text': 'статус', 'command': 'status', 'room': ''},
            created_at=NOW - timedelta(minutes=update_id),
        )
    JournalEntryFactory.create(kind='guests_set', subject='', payload={'update_id': 99, 'bot': '123456'})


def test_record_journals_the_command_with_its_update_and_the_bot(f_ledger: JournalCommandLedger) -> None:
    update = BotUpdate(update_id=42, chat_id='-1001234', text='холодно в гостиной', sent_at=NOW, edited=False)

    f_ledger.record(update, OwnerCommand(kind=CommandKind.WARMER, room=OPEN_SPACE), NOW)

    assert list(JournalEntry.objects.values_list('kind', 'subject', 'payload', 'created_at')) == [
        (
            'owner_command',
            'open-space',
            {'update_id': 42, 'bot': '123456', 'text': 'холодно в гостиной', 'command': 'warmer', 'room': 'open-space'},
            NOW,
        ),
    ]


def test_record_keeps_the_first_200_characters_of_the_text(f_ledger: JournalCommandLedger) -> None:
    update = BotUpdate(update_id=43, chat_id='-1001234', text='я' * 250, sent_at=NOW, edited=False)

    f_ledger.record(update, OwnerCommand(kind=CommandKind.HELP), NOW)

    assert JournalEntry.objects.get().payload['text'] == 'я' * 200


@pytest.mark.usefixtures('journaled_commands')
@pytest.mark.parametrize(
    ('token', 'expected'),
    [('123456:telegram-token', 41), ('654321:other-token', 57), ('777777:new-token', None)],
    ids=['newest_journaled_of_this_bot', 'another_bot', 'a_bot_without_commands'],
)
def test_last_update_id_is_the_one_journaled_last_by_the_same_bot(token: str, expected: int | None) -> None:
    ledger = JournalCommandLedger(journal=JournalRepository(), bot=token.partition(':')[0])

    assert ledger.last_update_id() == expected
