from datetime import UTC, datetime, timedelta

import pytest
from django.db import OperationalError
from structlog.testing import capture_logs

from apps.notifications.domain.bot import BotUpdate
from apps.notifications.domain.commands import CommandKind, CommandRoom, OwnerCommand
from apps.notifications.domain.errors import AlertDeliveryError, CommandPollError
from apps.notifications.stubs import CommandBotStub, CommandLedgerStub, CommandRoomsStub, OwnerCommandRunnerStub
from apps.notifications.usecases.receive_commands import (
    ReceiveCommandsInputDTO,
    ReceiveCommandsOutputDTO,
    ReceiveCommandsUseCase,
)

NOW = datetime(2026, 11, 4, 13, 40, tzinfo=UTC)
CHAT = '-1001234'
OPEN_SPACE = CommandRoom(slug='open-space', name='Гостиная, кухня и прихожая', level=1, stems=('гостин', 'кухн'))
WARMER = BotUpdate(update_id=42, chat_id=CHAT, text='холодно в гостиной', sent_at=NOW, edited=False)
STATUS = BotUpdate(update_id=43, chat_id=CHAT, text='статус', sent_at=NOW - timedelta(seconds=5), edited=False)
DROPPED = [
    BotUpdate(update_id=44, chat_id='42', text='статус', sent_at=NOW, edited=False),
    BotUpdate(update_id=45, chat_id=CHAT, text='жарко в гостиной', sent_at=NOW, edited=True),
    BotUpdate(update_id=46, chat_id=CHAT, text='гости', sent_at=NOW - timedelta(hours=1), edited=False),
]
RESUME = ReceiveCommandsInputDTO(offset=None, resume=True, now=NOW, clock_trusted=True)
BUSY = 'База данных занята: повторите через минуту.'


def _use_case(bot: CommandBotStub, ledger: CommandLedgerStub, runner: OwnerCommandRunnerStub) -> ReceiveCommandsUseCase:
    return ReceiveCommandsUseCase(
        bot=bot,
        chat_id=CHAT,
        ledger=ledger,
        rooms=CommandRoomsStub(rooms=[OPEN_SPACE]),
        runner=runner,
        language='ru',
    )


@pytest.fixture
def f_timeline() -> list[str]:
    return []


@pytest.fixture
def f_bot(request: pytest.FixtureRequest) -> CommandBotStub:
    return CommandBotStub(polls=(request.param,), send_error=None)


@pytest.fixture
def f_ledger(f_timeline: list[str]) -> CommandLedgerStub:
    return CommandLedgerStub(timeline=f_timeline, last=41, error=None)


@pytest.fixture
def f_runner(f_timeline: list[str]) -> OwnerCommandRunnerStub:
    return OwnerCommandRunnerStub(timeline=f_timeline)


@pytest.fixture
def f_use_case(
    f_bot: CommandBotStub,
    f_ledger: CommandLedgerStub,
    f_runner: OwnerCommandRunnerStub,
) -> ReceiveCommandsUseCase:
    return _use_case(f_bot, f_ledger, f_runner)


@pytest.fixture
def f_fresh_ledger(request: pytest.FixtureRequest, f_timeline: list[str]) -> CommandLedgerStub:
    return CommandLedgerStub(timeline=f_timeline, last=request.param, error=None)


@pytest.fixture
def f_fresh_use_case(
    f_bot: CommandBotStub,
    f_fresh_ledger: CommandLedgerStub,
    f_runner: OwnerCommandRunnerStub,
) -> ReceiveCommandsUseCase:
    return _use_case(f_bot, f_fresh_ledger, f_runner)


@pytest.fixture
def f_locked_ledger(f_timeline: list[str]) -> CommandLedgerStub:
    return CommandLedgerStub(timeline=f_timeline, last=41, error=OperationalError('database is locked'))


@pytest.fixture
def f_locked_use_case(
    f_bot: CommandBotStub,
    f_locked_ledger: CommandLedgerStub,
    f_runner: OwnerCommandRunnerStub,
) -> ReceiveCommandsUseCase:
    return _use_case(f_bot, f_locked_ledger, f_runner)


@pytest.fixture
def f_silent_bot() -> CommandBotStub:
    return CommandBotStub(polls=([WARMER],), send_error=AlertDeliveryError('telegram unreachable: timed out'))


@pytest.fixture
def f_silent_use_case(
    f_silent_bot: CommandBotStub,
    f_ledger: CommandLedgerStub,
    f_runner: OwnerCommandRunnerStub,
) -> ReceiveCommandsUseCase:
    return _use_case(f_silent_bot, f_ledger, f_runner)


@pytest.mark.parametrize(
    ('f_bot', 'f_fresh_ledger', 'asked'),
    [([], 41, 42), ([], None, None)],
    indirect=['f_bot', 'f_fresh_ledger'],
    ids=['after_the_last_journaled_update', 'nothing_journaled'],
)
def test_a_resumed_poll_starts_after_the_last_journaled_update(
    f_fresh_use_case: ReceiveCommandsUseCase,
    f_bot: CommandBotStub,
    asked: int | None,
) -> None:
    output = f_fresh_use_case.execute(RESUME)

    assert (output, f_bot.asked) == (ReceiveCommandsOutputDTO(offset=asked, answered=0), [(asked, 10)])


@pytest.mark.parametrize('f_bot', [[]], indirect=True)
def test_a_poll_goes_on_from_the_offset_it_is_given(
    f_use_case: ReceiveCommandsUseCase,
    f_bot: CommandBotStub,
    f_timeline: list[str],
) -> None:
    output = f_use_case.execute(ReceiveCommandsInputDTO(offset=40, resume=False, now=NOW, clock_trusted=True))

    assert (output, f_bot.asked, f_timeline) == (ReceiveCommandsOutputDTO(offset=40, answered=0), [(40, 10)], [])


@pytest.mark.parametrize('f_bot', [DROPPED], indirect=True)
def test_updates_from_another_chat_edits_and_stale_messages_advance_the_offset_and_nothing_else(
    f_use_case: ReceiveCommandsUseCase,
    f_bot: CommandBotStub,
    f_ledger: CommandLedgerStub,
    f_runner: OwnerCommandRunnerStub,
) -> None:
    output = f_use_case.execute(RESUME)

    assert (output, f_ledger.recorded, f_runner.runs, f_bot.sent) == (
        ReceiveCommandsOutputDTO(offset=47, answered=0),
        [],
        [],
        [],
    )


@pytest.mark.parametrize('f_bot', [[WARMER, STATUS]], indirect=True)
def test_each_command_is_journaled_before_it_runs_and_answered_once(
    f_use_case: ReceiveCommandsUseCase,
    f_bot: CommandBotStub,
    f_ledger: CommandLedgerStub,
    f_runner: OwnerCommandRunnerStub,
    f_timeline: list[str],
) -> None:
    with capture_logs() as logs:
        output = f_use_case.execute(ReceiveCommandsInputDTO(offset=None, resume=True, now=NOW, clock_trusted=False))

    warmer = OwnerCommand(kind=CommandKind.WARMER, room=OPEN_SPACE)
    status = OwnerCommand(kind=CommandKind.STATUS)
    assert (output, f_timeline, f_ledger.recorded, f_runner.runs, f_bot.sent, logs) == (
        ReceiveCommandsOutputDTO(offset=44, answered=2),
        ['read the last update', 'record 42', 'run warmer', 'record 43', 'run status'],
        [(WARMER, warmer, NOW), (STATUS, status, NOW)],
        [(warmer, NOW, False), (status, NOW, False)],
        ['warmer done', 'status done'],
        [
            {
                'event': 'owner_command_received',
                'update_id': 42,
                'command': 'warmer',
                'room': 'open-space',
                'log_level': 'info',
            },
            {'event': 'owner_command_received', 'update_id': 43, 'command': 'status', 'room': '', 'log_level': 'info'},
        ],
    )


@pytest.mark.parametrize('f_bot', [[WARMER]], indirect=True)
def test_a_command_the_journal_cannot_take_is_answered_busy_and_not_run(
    f_locked_use_case: ReceiveCommandsUseCase,
    f_bot: CommandBotStub,
    f_runner: OwnerCommandRunnerStub,
) -> None:
    with capture_logs() as logs:
        output = f_locked_use_case.execute(RESUME)

    assert (output, f_runner.runs, f_bot.sent, logs) == (
        ReceiveCommandsOutputDTO(offset=43, answered=1),
        [],
        [BUSY],
        [
            {
                'event': 'owner_command_not_recorded',
                'update_id': 42,
                'error': 'database is locked',
                'log_level': 'warning',
            },
        ],
    )


def test_a_reply_telegram_does_not_take_is_logged_and_the_offset_moves_on(
    f_silent_use_case: ReceiveCommandsUseCase,
    f_runner: OwnerCommandRunnerStub,
) -> None:
    with capture_logs() as logs:
        output = f_silent_use_case.execute(RESUME)

    assert (output, len(f_runner.runs), logs[-1]) == (
        ReceiveCommandsOutputDTO(offset=43, answered=1),
        1,
        {
            'event': 'owner_reply_failed',
            'update_id': 42,
            'error_code': 'alert_delivery_failed',
            'detail': 'telegram unreachable: timed out',
            'log_level': 'warning',
        },
    )


@pytest.mark.parametrize('f_bot', [CommandPollError('telegram unreachable: timed out')], indirect=True)
def test_a_failed_poll_propagates(f_use_case: ReceiveCommandsUseCase, f_runner: OwnerCommandRunnerStub) -> None:
    with pytest.raises(CommandPollError, match='telegram unreachable: timed out'):
        f_use_case.execute(RESUME)

    assert f_runner.runs == []


@pytest.mark.parametrize(
    ('f_bot', 'f_fresh_ledger'),
    [([WARMER], OperationalError('database is locked'))],
    indirect=True,
)
def test_a_resume_that_cannot_read_the_journal_skips_the_poll(
    f_fresh_use_case: ReceiveCommandsUseCase,
    f_bot: CommandBotStub,
) -> None:
    with capture_logs() as logs, pytest.raises(CommandPollError) as raised:
        f_fresh_use_case.execute(RESUME)

    assert (raised.value.detail, f_bot.asked, logs[0]) == (
        'the journal could not be read to resume the commands',
        [],
        {'event': 'owner_commands_offset_unread', 'error': 'database is locked', 'log_level': 'warning'},
    )
