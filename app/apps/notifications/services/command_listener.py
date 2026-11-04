import threading
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Protocol

import structlog
from django.conf import settings
from django.db import close_old_connections

from apps.core.domain.ports import Executable
from apps.core.services.secrets import SecretsFile, TelegramSecret
from apps.notifications.domain.bot import PollFailure
from apps.notifications.usecases.receive_commands import (
    ReceiveCommandsInputDTO,
    ReceiveCommandsOutputDTO,
    ReceiveCommandsUseCase,
)
from apps.system.domain.clock import ClockReading
from apps.system.services.clock_trust import clock_trust_source, read_clock

logger = structlog.get_logger(__name__)

RETRY_AFTER_SECONDS = 30.0
THREAD_NAME = 'teplo-owner-commands'

Receive = Executable[ReceiveCommandsInputDTO, ReceiveCommandsOutputDTO]


class StopSignal(Protocol):
    def is_set(self) -> bool: ...

    def set(self) -> None: ...

    def wait(self, timeout: float) -> bool: ...


class CommandListener:
    def __init__(
        self,
        secret: TelegramSecret | None,
        receive_factory: Callable[[TelegramSecret], Receive],
        read_clock: Callable[[], ClockReading],
        stop_signal: StopSignal,
        start_thread: Callable[[Callable[[], None]], None],
        retry_after: float,
    ) -> None:
        self._secret = secret
        self._receive_factory = receive_factory
        self._read_clock = read_clock
        self._stop_signal = stop_signal
        self._start_thread = start_thread
        self._retry_after = retry_after

    @classmethod
    def init(cls) -> 'CommandListener':
        secret = SecretsFile.init(Path(settings.TEPLO_SECRETS_FILE)).telegram
        return cls(
            secret=secret if secret is not None and secret.commands else None,
            receive_factory=ReceiveCommandsUseCase.init,
            read_clock=partial(read_clock, clock_trust_source()),
            stop_signal=threading.Event(),
            start_thread=_start_daemon,
            retry_after=RETRY_AFTER_SECONDS,
        )

    def start(self) -> None:
        if self._secret is None:
            logger.info('owner_commands_disabled')
            return

        self._start_thread(partial(self._listen, self._receive_factory(self._secret)))
        logger.info('owner_commands_started')

    def stop(self) -> None:
        self._stop_signal.set()

    def _listen(self, receive: Receive) -> None:
        offset: int | None = None
        resume = True
        failing = False
        waiting = False
        while not self._stop_signal.is_set():
            try:
                reading = self._read_clock()
                if not reading.trusted:
                    waiting = self._wait_for_clock(waiting=waiting)
                    continue

                waiting = False
                output = self._receive(receive, reading, offset, resume=resume)
            except Exception:
                # An exception raised here would end the listener thread, and with it every later command.
                logger.exception('owner_commands_failed')
                resume = True
                self._stop_signal.wait(self._retry_after)
                continue

            if output.failure is not None:
                failing = self._poll_failed(output.failure, failing=failing)
                continue

            if failing:
                logger.info('owner_commands_poll_recovered')
            offset, resume, failing = output.offset, False, False

    def _poll_failed(self, failure: PollFailure, *, failing: bool) -> bool:
        if not failing:
            logger.warning('owner_commands_poll_failed', error_code=failure.error_code, detail=failure.detail)
        self._stop_signal.wait(self._retry_after)
        return True

    def _wait_for_clock(self, *, waiting: bool) -> bool:
        if not waiting:
            logger.info('owner_commands_wait_for_clock')
        self._stop_signal.wait(self._retry_after)
        return True

    def _receive(
        self,
        receive: Receive,
        reading: ClockReading,
        offset: int | None,
        *,
        resume: bool,
    ) -> ReceiveCommandsOutputDTO:
        close_old_connections()
        try:
            return receive.execute(
                ReceiveCommandsInputDTO(
                    offset=offset,
                    resume=resume,
                    now=reading.now,
                    clock_trusted=reading.trusted,
                ),
            )
        finally:
            close_old_connections()


def _start_daemon(target: Callable[[], None]) -> None:
    threading.Thread(target=target, name=THREAD_NAME, daemon=True).start()


command_listener = CommandListener.init()
