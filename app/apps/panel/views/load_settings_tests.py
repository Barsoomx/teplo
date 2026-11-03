from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.test import Client

from apps.core.domain.errors import SettingsRejectedError
from apps.core.domain.settings import SettingProblem
from apps.panel.usecases.get_settings_page import GetSettingsPageOutputDTO
from apps.power.usecases.update_load_settings import UpdateLoadSettingsInputDTO, UpdateLoadSettingsOutputDTO

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
POSTED = {
    'start_ceiling_amps': '21.0',
    'shed_amps': '25.0',
    'shed_hold_seconds': '5',
    'restore_amps': '18.0',
    'restore_hold_seconds': '120',
    'start_interval_seconds': '30',
    'meter_lost_after_seconds': '60',
    'default_load_amps': '6.0',
}


@pytest.fixture
def m_settings_use_case(settings_page: GetSettingsPageOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.load_settings.GetSettingsPageUseCase') as use_case:
        use_case.init.return_value.execute.return_value = settings_page
        yield use_case


@pytest.fixture
def m_update_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.load_settings.UpdateLoadSettingsUseCase') as use_case:
        use_case.init.return_value.execute.return_value = UpdateLoadSettingsOutputDTO(changes=())
        yield use_case


@pytest.fixture
def m_rejecting_update_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.load_settings.UpdateLoadSettingsUseCase') as use_case:
        use_case.init.return_value.execute.side_effect = SettingsRejectedError(
            (SettingProblem(field='restore_amps', code='above_start_ceiling'),),
        )
        yield use_case


def test_saved_load_settings_go_to_the_use_case_without_the_load_management_gate(
    f_russian_client: Client,
    m_settings_use_case: MagicMock,
    m_update_use_case: MagicMock,
) -> None:
    response = f_russian_client.post('/settings/load-management/', {**POSTED, 'load_management_live': 'on'})

    executed = m_update_use_case.init.return_value.execute.call_args.args[0]
    content = response.content.decode()
    assert (
        response.status_code,
        replace(executed, now=NOW),
        hasattr(executed, 'load_management_live'),
        'Ничего не изменилось' in content,
        'id="load-management-settings"' in content,
        m_settings_use_case.init.call_count,
    ) == (
        200,
        UpdateLoadSettingsInputDTO(
            start_ceiling_amps=Decimal('21.0'),
            shed_amps=Decimal('25.0'),
            shed_hold_seconds=5,
            restore_amps=Decimal('18.0'),
            restore_hold_seconds=120,
            start_interval_seconds=30,
            meter_lost_after_seconds=60,
            default_load_amps=Decimal('6.0'),
            now=NOW,
        ),
        False,
        True,
        True,
        1,
    )


def test_thresholds_out_of_order_are_explained_at_their_field(
    f_russian_client: Client,
    m_settings_use_case: MagicMock,
    m_rejecting_update_use_case: MagicMock,
) -> None:
    response = f_russian_client.post('/settings/load-management/', {**POSTED, 'restore_amps': '22.0'})

    content = response.content.decode()
    assert (
        response.status_code,
        'Не может быть выше порога включения' in content,
        'name="restore_amps" value="22.0"' in content,
        m_rejecting_update_use_case.init.call_count,
        m_settings_use_case.init.call_count,
    ) == (200, True, True, 1, 1)


def test_load_setting_out_of_range_is_refused_before_the_use_case(
    f_client: Client,
    m_settings_use_case: MagicMock,
    m_update_use_case: MagicMock,
) -> None:
    response = f_client.post('/settings/load-management/', {**POSTED, 'shed_hold_seconds': '0'})

    assert (
        response.status_code,
        'Ensure this value is greater than or equal to 1.' in response.content.decode(),
        m_update_use_case.init.call_count,
        m_settings_use_case.init.call_count,
    ) == (200, True, 0, 1)


def test_saving_load_settings_requires_login(f_anonymous_client: Client, m_update_use_case: MagicMock) -> None:
    response = f_anonymous_client.post('/settings/load-management/', POSTED)

    assert (response.status_code, response['Location'], m_update_use_case.init.call_count) == (
        302,
        '/login/?next=/settings/load-management/',
        0,
    )
