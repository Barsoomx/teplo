from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.db import IntegrityError, OperationalError
from django.test import Client

from apps.core.domain.errors import SettingsRejectedError
from apps.core.domain.settings import SettingChange, SettingProblem
from apps.panel.usecases.get_settings_page import GetSettingsPageOutputDTO
from apps.power.domain.errors import LoadNotFoundError
from apps.power.domain.load_config import LoadConfig
from apps.power.domain.loads import LoadPhase
from apps.power.usecases.update_loads import LoadEdit, UpdateLoadsInputDTO, UpdateLoadsOutputDTO

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
POSTED = {
    'loads-TOTAL_FORMS': '2',
    'loads-INITIAL_FORMS': '2',
    'loads-MIN_NUM_FORMS': '0',
    'loads-MAX_NUM_FORMS': '1000',
    'loads-0-slug': 'boiler-1f',
    'loads-0-priority': '2',
    'loads-0-phase': 'unknown',
    'loads-0-nominal_amps': '6.0',
    'loads-1-slug': 'kitchen',
    'loads-1-priority': '1',
    'loads-1-phase': 'l2',
    'loads-1-nominal_amps': '',
}
SWAPPED = UpdateLoadsInputDTO(
    loads=(
        LoadEdit(slug='boiler-1f', config=LoadConfig(priority=2, phase=LoadPhase.UNKNOWN, nominal_amps=Decimal('6.0'))),
        LoadEdit(slug='kitchen', config=LoadConfig(priority=1, phase=LoadPhase.L2, nominal_amps=None)),
    ),
    now=NOW,
)


@pytest.fixture
def m_settings_use_case(settings_page: GetSettingsPageOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.loads_settings.GetSettingsPageUseCase') as use_case:
        use_case.init.return_value.execute.return_value = settings_page
        yield use_case


@pytest.fixture
def m_update_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.loads_settings.UpdateLoadsUseCase') as use_case:
        use_case.init.return_value.execute.return_value = UpdateLoadsOutputDTO(
            changes=(
                SettingChange(target='load', subject='boiler-1f', name='Бойлер 1 этаж', field='priority', old=1, new=2),
                SettingChange(target='load', subject='kitchen', name='Кухня', field='priority', old=2, new=1),
            ),
        )
        yield use_case


@pytest.fixture
def m_rejecting_update_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.loads_settings.UpdateLoadsUseCase') as use_case:
        use_case.init.return_value.execute.side_effect = SettingsRejectedError(
            (SettingProblem(field='priority', code='priority_taken', subject='kitchen'),),
        )
        yield use_case


@pytest.fixture
def m_missing_load_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.loads_settings.UpdateLoadsUseCase') as use_case:
        use_case.init.return_value.execute.side_effect = LoadNotFoundError('kitchen')
        yield use_case


@pytest.fixture
def m_update_use_case_the_database_refuses(request: pytest.FixtureRequest) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.loads_settings.UpdateLoadsUseCase') as use_case:
        use_case.init.return_value.execute.side_effect = request.param
        yield use_case


def test_swapped_priorities_go_to_the_use_case_in_one_save(
    f_russian_client: Client,
    m_settings_use_case: MagicMock,
    m_update_use_case: MagicMock,
) -> None:
    response = f_russian_client.post('/settings/loads/', {**POSTED, 'loads-0-live': 'on', 'loads-1-live': 'on'})

    executed = m_update_use_case.init.return_value.execute.call_args.args[0]
    content = response.content.decode()
    assert (
        response.status_code,
        replace(executed, now=NOW),
        'Сохранено. Управление нагрузкой учтёт это в течение минуты.' in content,
        'id="loads-settings"' in content,
        m_settings_use_case.init.call_count,
    ) == (200, SWAPPED, True, True, 1)


def test_empty_row_added_to_the_form_is_left_out(
    f_client: Client,
    m_settings_use_case: MagicMock,
    m_update_use_case: MagicMock,
) -> None:
    f_client.post('/settings/loads/', {**POSTED, 'loads-TOTAL_FORMS': '3'})

    executed = m_update_use_case.init.return_value.execute.call_args.args[0]
    assert (replace(executed, now=NOW), m_settings_use_case.init.call_count) == (SWAPPED, 1)


def test_priority_another_load_keeps_is_explained_on_its_row(
    f_russian_client: Client,
    m_settings_use_case: MagicMock,
    m_rejecting_update_use_case: MagicMock,
) -> None:
    response = f_russian_client.post('/settings/loads/', {**POSTED, 'loads-1-priority': '2'})

    content = response.content.decode()
    assert (
        response.status_code,
        content.count('Этот приоритет уже у другой нагрузки'),
        'name="loads-1-priority" value="2"' in content,
        'Сохранено' in content,
        m_rejecting_update_use_case.init.call_count,
        m_settings_use_case.init.call_count,
    ) == (200, 1, True, False, 1, 1)


def test_row_out_of_range_is_refused_before_the_use_case(
    f_client: Client,
    m_settings_use_case: MagicMock,
    m_update_use_case: MagicMock,
) -> None:
    response = f_client.post('/settings/loads/', {**POSTED, 'loads-0-phase': 'l4'})

    assert (
        response.status_code,
        'Select a valid choice.' in response.content.decode(),
        m_update_use_case.init.call_count,
        m_settings_use_case.init.call_count,
    ) == (200, True, 0, 1)


def test_settings_of_a_load_that_is_gone_return_404(
    f_client: Client,
    m_settings_use_case: MagicMock,
    m_missing_load_use_case: MagicMock,
) -> None:
    response = f_client.post('/settings/loads/', POSTED)

    assert (
        response.status_code,
        m_missing_load_use_case.init.call_count,
        m_settings_use_case.init.call_count,
    ) == (404, 1, 0)


def test_saving_loads_requires_login(f_anonymous_client: Client, m_update_use_case: MagicMock) -> None:
    response = f_anonymous_client.post('/settings/loads/', POSTED)

    assert (response.status_code, response['Location'], m_update_use_case.init.call_count) == (
        302,
        '/login/?next=/settings/loads/',
        0,
    )


@pytest.mark.parametrize(
    'm_update_use_case_the_database_refuses',
    [OperationalError('database is locked'), IntegrityError('CHECK constraint failed')],
    ids=['database_locked', 'check_failed'],
    indirect=True,
)
def test_save_the_database_does_not_take_is_answered_on_the_card_with_what_was_typed(
    f_russian_client: Client,
    m_settings_use_case: MagicMock,
    m_update_use_case_the_database_refuses: MagicMock,
) -> None:
    response = f_russian_client.post('/settings/loads/', POSTED)

    content = response.content.decode()
    assert (
        response.status_code,
        content.count('Не сохранено: база данных не приняла изменение. Попробуйте ещё раз'),
        'name="loads-1-priority" value="1"' in content,
        'Сохранено' in content,
        m_update_use_case_the_database_refuses.init.call_count,
        m_settings_use_case.init.call_count,
    ) == (200, 1, True, False, 1, 1)
