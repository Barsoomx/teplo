from datetime import UTC, datetime, timedelta

import pytest

from apps.notifications.domain.bot import BotUpdate, accepted

NOW = datetime(2026, 11, 4, 13, 40, tzinfo=UTC)
CHAT = '-1001234'
COMMAND = BotUpdate(update_id=41, chat_id=CHAT, text='холодно в гостиной', sent_at=NOW, edited=False)


@pytest.mark.parametrize(
    ('update', 'expected'),
    [
        (COMMAND, True),
        (BotUpdate(update_id=41, chat_id='42', text='статус', sent_at=NOW, edited=False), False),
        (BotUpdate(update_id=41, chat_id=CHAT, text='жарко в гостиной', sent_at=NOW, edited=True), False),
        (BotUpdate(update_id=41, chat_id=CHAT, text=None, sent_at=NOW, edited=False), False),
        (BotUpdate(update_id=41, chat_id=CHAT, text='  ', sent_at=NOW, edited=False), False),
        (BotUpdate(update_id=41, chat_id=CHAT, text='статус', sent_at=None, edited=False), False),
        (
            BotUpdate(update_id=41, chat_id=CHAT, text='гости', sent_at=NOW - timedelta(minutes=10), edited=False),
            True,
        ),
        (
            BotUpdate(
                update_id=41,
                chat_id=CHAT,
                text='гости',
                sent_at=NOW - timedelta(minutes=10, seconds=1),
                edited=False,
            ),
            False,
        ),
        (BotUpdate(update_id=41, chat_id=CHAT, text='гости', sent_at=NOW + timedelta(hours=5), edited=False), True),
    ],
    ids=[
        'command',
        'another_chat',
        'edited',
        'without_text',
        'blank_text',
        'without_date',
        'ten_minutes_old',
        'older_than_ten_minutes',
        'from_ahead_of_a_clock_behind',
    ],
)
def test_only_fresh_unedited_text_from_the_owner_s_chat_is_accepted(update: BotUpdate, expected: bool) -> None:
    assert accepted(update, CHAT, NOW) is expected
