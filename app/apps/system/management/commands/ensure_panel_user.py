from django.core.management.base import BaseCommand

from apps.system.usecases.ensure_panel_user import EnsurePanelUserInputDTO, EnsurePanelUserUseCase

OWNER_USERNAME = 'owner'


class Command(BaseCommand):
    help = 'Create or update the panel owner account from the secrets file'

    def handle(self, *_args: object, **_options: object) -> None:
        output = EnsurePanelUserUseCase.init().execute(EnsurePanelUserInputDTO(username=OWNER_USERNAME))
        self.stdout.write(f'panel user {output.username} {"created" if output.created else "updated"}')
