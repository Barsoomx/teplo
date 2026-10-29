import pytest
from django.contrib.auth.models import User

from apps.core.domain.errors import SecretMissingError
from apps.core.services.secrets import SecretsFile
from apps.system.repositories.panel_user import PanelUserRepository
from apps.system.usecases.ensure_panel_user import (
    EnsurePanelUserInputDTO,
    EnsurePanelUserOutputDTO,
    EnsurePanelUserUseCase,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def f_use_case() -> EnsurePanelUserUseCase:
    secrets = SecretsFile(devices={}, panel_password='panel-secret')
    return EnsurePanelUserUseCase(secrets=secrets, users=PanelUserRepository())


@pytest.fixture
def f_no_password_use_case() -> EnsurePanelUserUseCase:
    return EnsurePanelUserUseCase(secrets=SecretsFile(devices={}, panel_password=None), users=PanelUserRepository())


def test_creates_owner_superuser(f_use_case: EnsurePanelUserUseCase) -> None:
    output = f_use_case.execute(EnsurePanelUserInputDTO(username='owner'))

    owner = User.objects.get(username='owner')
    assert (output, owner.check_password('panel-secret'), owner.is_superuser, owner.is_staff) == (
        EnsurePanelUserOutputDTO(username='owner', created=True),
        True,
        True,
        True,
    )


def test_updates_existing_owner(f_use_case: EnsurePanelUserUseCase, f_owner: User) -> None:
    output = f_use_case.execute(EnsurePanelUserInputDTO(username=f_owner.username))

    assert (output.created, User.objects.get(pk=f_owner.pk).check_password('panel-secret')) == (False, True)


def test_missing_password_is_rejected(f_no_password_use_case: EnsurePanelUserUseCase) -> None:
    with pytest.raises(SecretMissingError):
        f_no_password_use_case.execute(EnsurePanelUserInputDTO(username='owner'))
