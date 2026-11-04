from dataclasses import dataclass
from datetime import datetime

import structlog
from django.conf import settings
from django.db import OperationalError
from django.utils import translation

from apps.core.domain.usecases.base import BaseUseCase
from apps.core.services.secrets import TelegramSecret
from apps.notifications.domain.bot import BotUpdate, PollFailure, accepted
from apps.notifications.domain.commands import OwnerCommand, parse_command
from apps.notifications.domain.errors import AlertDeliveryError, AlertRejectedError, CommandPollError
from apps.notifications.domain.ports import CommandBot, CommandLedger, CommandRooms, OwnerCommandRunner
from apps.notifications.services.climate_commands import ClimateCommandRunner
from apps.notifications.services.command_ledger import JournalCommandLedger
from apps.notifications.services.command_replies import BUSY
from apps.notifications.services.command_rooms import HouseCommandRooms
from apps.notifications.services.telegram import TelegramClient

logger = structlog.get_logger(__name__)

POLL_SECONDS = 10
UNREAD_OFFSET = 'the journal could not be read to resume the commands'


@dataclass(frozen=True, kw_only=True)
class ReceiveCommandsInputDTO:
    offset: int | None
    resume: bool
    now: datetime
    clock_trusted: bool


@dataclass(frozen=True, kw_only=True)
class ReceiveCommandsOutputDTO:
    offset: int | None
    answered: int
    failure: PollFailure | None = None


class ReceiveCommandsUseCase(BaseUseCase[ReceiveCommandsInputDTO, ReceiveCommandsOutputDTO]):
    def __init__(
        self,
        bot: CommandBot,
        chat_id: str,
        ledger: CommandLedger,
        rooms: CommandRooms,
        runner: OwnerCommandRunner,
        language: str,
    ) -> None:
        super().__init__()
        self._bot = bot
        self._chat_id = chat_id
        self._ledger = ledger
        self._rooms = rooms
        self._runner = runner
        self._language = language

    @classmethod
    def init(cls, secret: TelegramSecret) -> 'ReceiveCommandsUseCase':
        return cls(
            bot=TelegramClient.init(secret),
            chat_id=secret.chat_id,
            ledger=JournalCommandLedger.init(secret),
            rooms=HouseCommandRooms.init(),
            runner=ClimateCommandRunner.init(),
            language=settings.TEPLO_ALERT_LANGUAGE,
        )

    def _execute(self, input_dto: ReceiveCommandsInputDTO) -> ReceiveCommandsOutputDTO:
        try:
            offset = self._offset(input_dto)
            updates = self._bot.updates(offset, POLL_SECONDS)
        except CommandPollError as error:
            return ReceiveCommandsOutputDTO(
                offset=input_dto.offset,
                answered=0,
                failure=PollFailure(error_code=error.error_code, detail=error.detail),
            )

        answered = 0
        for update in updates:
            offset = update.update_id + 1
            if accepted(update, self._chat_id, input_dto.now):
                self._answer(update, input_dto)
                answered += 1
        return ReceiveCommandsOutputDTO(offset=offset, answered=answered)

    def _offset(self, input_dto: ReceiveCommandsInputDTO) -> int | None:
        if not input_dto.resume:
            return input_dto.offset

        try:
            last = self._ledger.last_update_id()
        except OperationalError as error:
            raise CommandPollError(f'{UNREAD_OFFSET}: {error}') from None

        return None if last is None else last + 1

    def _answer(self, update: BotUpdate, input_dto: ReceiveCommandsInputDTO) -> None:
        try:
            command = self._recorded(update, input_dto.now)
        except OperationalError as error:
            logger.warning('owner_command_not_recorded', update_id=update.update_id, error=str(error))
            with translation.override(self._language):
                busy = str(BUSY)
            self._reply(update, busy)
            return

        logger.info(
            'owner_command_received',
            update_id=update.update_id,
            command=command.kind.value,
            room='' if command.room is None else command.room.slug,
        )
        self._reply(update, self._runner.run(command, input_dto.now, clock_trusted=input_dto.clock_trusted))

    def _recorded(self, update: BotUpdate, now: datetime) -> OwnerCommand:
        command = parse_command(update.text or '', self._rooms.rooms())
        self._ledger.record(update, command, now)
        return command

    def _reply(self, update: BotUpdate, text: str) -> None:
        try:
            self._bot.send(text)
        except (AlertDeliveryError, AlertRejectedError) as error:
            logger.warning(
                'owner_reply_failed',
                update_id=update.update_id,
                error_code=error.error_code,
                detail=error.detail,
            )
