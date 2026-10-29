from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from apps.core.domain.errors import SecretMissingError
from apps.core.domain.usecases.base import BaseUseCase
from apps.core.services.secrets import SecretsFile
from apps.system.repositories.panel_user import PanelUserRepository


@dataclass(frozen=True, kw_only=True)
class EnsurePanelUserInputDTO:
    username: str


@dataclass(frozen=True, kw_only=True)
class EnsurePanelUserOutputDTO:
    username: str
    created: bool


class EnsurePanelUserUseCase(BaseUseCase[EnsurePanelUserInputDTO, EnsurePanelUserOutputDTO]):
    def __init__(self, secrets: SecretsFile, users: PanelUserRepository) -> None:
        super().__init__()
        self._secrets = secrets
        self._users = users

    @classmethod
    def init(cls) -> 'EnsurePanelUserUseCase':
        return cls(secrets=SecretsFile.init(Path(settings.TEPLO_SECRETS_FILE)), users=PanelUserRepository())

    def _execute(self, input_dto: EnsurePanelUserInputDTO) -> EnsurePanelUserOutputDTO:
        password = self._secrets.panel_password
        if not password:
            raise SecretMissingError('panel.password')

        created = self._users.upsert_superuser(input_dto.username, password)
        return EnsurePanelUserOutputDTO(username=input_dto.username, created=created)
