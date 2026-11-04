from datetime import UTC, datetime, timedelta
from functools import partial

import pytest
from structlog.testing import capture_logs

from apps.core.services.secrets import TelegramSecret
from apps.notifications.domain.errors import CommandPollError
from apps.notifications.services.command_listener import CommandListener, Receive
from apps.notifications.stubs import ExecutableStub, StartThreadStub, StopSignalStub
from apps.notifications.usecases.receive_commands import ReceiveCommandsInputDTO, ReceiveCommandsOutputDTO
from apps.system.domain.clock import ClockReading
from apps.system.stubs import ClockReadingsStub

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 4, 13, 40, tzinfo=UTC)
SECRET = TelegramSecret(token='123456:telegram-token', chat_id='-1001234', proxy=None, commands=True)
TIMED_OUT = CommandPollError('telegram unreachable: timed out')
STARTED = {'event': 'owner_commands_started', 'log_level': 'info'}
TRUSTED = ClockReading(now=NOW, monotonic=0.0, trusted=True)
UNTRUSTED = ClockReading(now=NOW - timedelta(hours=5), monotonic=0.0, trusted=False)
POLL_FAILED = {
    'event': 'owner_commands_poll_failed',
    'error_code': 'owner_commands_unreachable',
    'detail': 'telegram unreachable: timed out',
    'log_level': 'warning',
}


def _input(offset: int | None, *, resume: bool) -> ReceiveCommandsInputDTO:
    return ReceiveCommandsInputDTO(offset=offset, resume=resume, now=NOW, clock_trusted=True)


def _answered(offset: int) -> ReceiveCommandsOutputDTO:
    return ReceiveCommandsOutputDTO(offset=offset, answered=1)


class ReceiveFactoryStub:
    def __init__(self, receive: Receive) -> None:
        self._receive = receive
        self._secrets: list[TelegramSecret] = []

    @property
    def secrets(self) -> list[TelegramSecret]:
        return self._secrets

    def __call__(self, secret: TelegramSecret) -> Receive:
        self._secrets.append(secret)
        return self._receive


@pytest.fixture
def f_receive(request: pytest.FixtureRequest) -> ExecutableStub[ReceiveCommandsInputDTO, ReceiveCommandsOutputDTO]:
    return ExecutableStub(outcomes=request.param)


@pytest.fixture
def f_factory(f_receive: ExecutableStub[ReceiveCommandsInputDTO, ReceiveCommandsOutputDTO]) -> ReceiveFactoryStub:
    return ReceiveFactoryStub(receive=f_receive)


@pytest.fixture
def f_signal(request: pytest.FixtureRequest) -> StopSignalStub:
    return StopSignalStub(rounds=request.param)


@pytest.fixture
def f_thread() -> StartThreadStub:
    return StartThreadStub()


@pytest.fixture
def f_listener(f_factory: ReceiveFactoryStub, f_signal: StopSignalStub, f_thread: StartThreadStub) -> CommandListener:
    return CommandListener(
        secret=SECRET,
        receive_factory=f_factory,
        read_clock=partial(ClockReading, now=NOW, monotonic=0.0, trusted=True),
        stop_signal=f_signal,
        start_thread=f_thread,
        retry_after=30.0,
    )


@pytest.fixture
def f_syncing_listener(
    f_factory: ReceiveFactoryStub,
    f_signal: StopSignalStub,
    f_thread: StartThreadStub,
) -> CommandListener:
    return CommandListener(
        secret=SECRET,
        receive_factory=f_factory,
        read_clock=ClockReadingsStub(readings=(UNTRUSTED, UNTRUSTED, TRUSTED)),
        stop_signal=f_signal,
        start_thread=f_thread,
        retry_after=30.0,
    )


@pytest.fixture
def f_disabled_listener(f_factory: ReceiveFactoryStub, f_thread: StartThreadStub) -> CommandListener:
    return CommandListener(
        secret=None,
        receive_factory=f_factory,
        read_clock=partial(ClockReading, now=NOW, monotonic=0.0, trusted=True),
        stop_signal=StopSignalStub(rounds=1),
        start_thread=f_thread,
        retry_after=30.0,
    )


@pytest.fixture
def f_stopped_listener(f_listener: CommandListener) -> CommandListener:
    f_listener.stop()
    return f_listener


@pytest.mark.parametrize('f_receive', [()], indirect=True)
def test_without_commands_switched_on_nothing_polls_and_the_log_says_so(
    f_disabled_listener: CommandListener,
    f_factory: ReceiveFactoryStub,
    f_thread: StartThreadStub,
) -> None:
    with capture_logs() as logs:
        f_disabled_listener.start()

    assert (f_factory.secrets, f_thread.started, logs) == (
        [],
        0,
        [{'event': 'owner_commands_disabled', 'log_level': 'info'}],
    )


@pytest.mark.parametrize(('f_receive', 'f_signal'), [((_answered(42), _answered(43)), 2)], indirect=True)
def test_the_listener_resumes_from_the_journal_once_then_polls_on_from_each_offset(
    f_listener: CommandListener,
    f_receive: ExecutableStub[ReceiveCommandsInputDTO, ReceiveCommandsOutputDTO],
    f_factory: ReceiveFactoryStub,
    f_thread: StartThreadStub,
    f_signal: StopSignalStub,
) -> None:
    with capture_logs() as logs:
        f_listener.start()

    assert (f_factory.secrets, f_thread.started, f_receive.inputs, f_signal.waits, logs) == (
        [SECRET],
        1,
        [_input(None, resume=True), _input(42, resume=False)],
        [],
        [STARTED],
    )


@pytest.mark.parametrize(
    ('f_receive', 'f_signal'),
    [((TIMED_OUT, TIMED_OUT, _answered(42), TIMED_OUT), 4)],
    indirect=True,
)
def test_a_failed_poll_waits_30_seconds_and_is_logged_once_a_streak(
    f_listener: CommandListener,
    f_receive: ExecutableStub[ReceiveCommandsInputDTO, ReceiveCommandsOutputDTO],
    f_signal: StopSignalStub,
) -> None:
    with capture_logs() as logs:
        f_listener.start()

    assert (f_receive.inputs, f_signal.waits, logs) == (
        [_input(None, resume=True), _input(None, resume=True), _input(None, resume=True), _input(42, resume=False)],
        [30.0, 30.0, 30.0],
        [POLL_FAILED, {'event': 'owner_commands_poll_recovered', 'log_level': 'info'}, POLL_FAILED, STARTED],
    )


@pytest.mark.parametrize(('f_receive', 'f_signal'), [((_answered(42),), 3)], indirect=True)
def test_the_listener_polls_only_once_the_clock_is_synchronised(
    f_syncing_listener: CommandListener,
    f_receive: ExecutableStub[ReceiveCommandsInputDTO, ReceiveCommandsOutputDTO],
    f_signal: StopSignalStub,
) -> None:
    with capture_logs() as logs:
        f_syncing_listener.start()

    assert (f_receive.inputs, f_signal.waits, logs) == (
        [_input(None, resume=True)],
        [30.0, 30.0],
        [{'event': 'owner_commands_wait_for_clock', 'log_level': 'info'}, STARTED],
    )


@pytest.mark.parametrize(
    ('f_receive', 'f_signal'),
    [((_answered(42), RuntimeError('boom'), _answered(44)), 3)],
    indirect=True,
)
def test_an_unexpected_failure_is_logged_waited_out_and_resumed_from_the_journal(
    f_listener: CommandListener,
    f_receive: ExecutableStub[ReceiveCommandsInputDTO, ReceiveCommandsOutputDTO],
    f_signal: StopSignalStub,
) -> None:
    with capture_logs() as logs:
        f_listener.start()

    assert (f_receive.inputs, f_signal.waits, logs) == (
        [_input(None, resume=True), _input(42, resume=False), _input(42, resume=True)],
        [30.0],
        [{'event': 'owner_commands_failed', 'exc_info': True, 'log_level': 'error'}, STARTED],
    )


@pytest.mark.parametrize(('f_receive', 'f_signal'), [((_answered(42),), 5)], indirect=True)
def test_a_stopped_listener_polls_no_more(
    f_stopped_listener: CommandListener,
    f_receive: ExecutableStub[ReceiveCommandsInputDTO, ReceiveCommandsOutputDTO],
    f_signal: StopSignalStub,
) -> None:
    f_stopped_listener.start()

    assert (f_signal.stopped, f_receive.inputs) == (True, [])
