from django.core.management.base import BaseCommand, CommandError

from apps.core.domain.errors import DomainError
from apps.notifications.usecases.send_test_alert import SendTestAlertInputDTO, SendTestAlertUseCase


class Command(BaseCommand):
    help = 'Send a test message to the Telegram chat from the secrets file'

    def handle(self, *_args: object, **_options: object) -> None:
        try:
            SendTestAlertUseCase.init().execute(SendTestAlertInputDTO())
        except DomainError as error:
            raise CommandError(f'{error.error_code}: {error.detail}') from None

        self.stdout.write('ok')
