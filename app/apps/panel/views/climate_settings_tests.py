from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.test import Client

from apps.climate.usecases.update_climate_settings import (
    UpdateClimateSettingsInputDTO,
    UpdateClimateSettingsOutputDTO,
)
from apps.core.domain.errors import SettingsRejectedError
from apps.core.domain.settings import SettingChange, SettingProblem
from apps.panel.usecases.get_settings_page import GetSettingsPageOutputDTO

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
POSTED = {
    'air_start_offset': '0.25',
    'initial_early_stop_offset': '0.30',
    'learned_mornings_needed': '5',
    'preheat_lead_cap_minutes': '210',
    'second_floor_guard_threshold': '18.5',
    'second_floor_guard_hysteresis': '0.50',
}


@pytest.fixture
def m_settings_use_case(settings_page: GetSettingsPageOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.climate_settings.GetSettingsPageUseCase') as use_case:
        use_case.init.return_value.execute.return_value = settings_page
        yield use_case


@pytest.fixture
def m_update_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.climate_settings.UpdateClimateSettingsUseCase') as use_case:
        use_case.init.return_value.execute.return_value = UpdateClimateSettingsOutputDTO(
            changes=(
                SettingChange(
                    target='climate_settings',
                    subject='',
                    name='',
                    field='air_start_offset',
                    old='0.30',
                    new='0.25',
                ),
            ),
        )
        yield use_case


@pytest.fixture
def m_rejecting_update_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.climate_settings.UpdateClimateSettingsUseCase') as use_case:
        use_case.init.return_value.execute.side_effect = SettingsRejectedError(
            (SettingProblem(field='preheat_lead_cap_minutes', code='out_of_range'),),
        )
        yield use_case


def test_saved_climate_settings_go_to_the_use_case_without_the_guard_gate(
    f_russian_client: Client,
    m_settings_use_case: MagicMock,
    m_update_use_case: MagicMock,
) -> None:
    response = f_russian_client.post('/settings/climate/', {**POSTED, 'second_floor_guard_live': 'on'})

    executed = m_update_use_case.init.return_value.execute.call_args.args[0]
    content = response.content.decode()
    assert (
        response.status_code,
        replace(executed, now=NOW),
        hasattr(executed, 'second_floor_guard_live'),
        'Сохранено. Цикл отопления учтёт это в течение минуты.' in content,
        'id="climate-settings"' in content,
        m_settings_use_case.init.call_count,
    ) == (
        200,
        UpdateClimateSettingsInputDTO(
            air_start_offset=Decimal('0.25'),
            initial_early_stop_offset=Decimal('0.30'),
            learned_mornings_needed=5,
            preheat_lead_cap_minutes=210,
            second_floor_guard_threshold=Decimal('18.5'),
            second_floor_guard_hysteresis=Decimal('0.50'),
            now=NOW,
        ),
        False,
        True,
        True,
        1,
    )


def test_refused_climate_setting_is_explained_at_its_field(
    f_russian_client: Client,
    m_settings_use_case: MagicMock,
    m_rejecting_update_use_case: MagicMock,
) -> None:
    response = f_russian_client.post('/settings/climate/', POSTED)

    content = response.content.decode()
    assert (
        response.status_code,
        'Вне допустимого диапазона' in content,
        'name="preheat_lead_cap_minutes" value="210"' in content,
        m_rejecting_update_use_case.init.call_count,
        m_settings_use_case.init.call_count,
    ) == (200, True, True, 1, 1)


def test_climate_setting_out_of_range_is_refused_before_the_use_case(
    f_client: Client,
    m_settings_use_case: MagicMock,
    m_update_use_case: MagicMock,
) -> None:
    response = f_client.post('/settings/climate/', {**POSTED, 'initial_early_stop_offset': '1.6'})

    assert (
        response.status_code,
        'Ensure this value is less than or equal to 1.50.' in response.content.decode(),
        m_update_use_case.init.call_count,
        m_settings_use_case.init.call_count,
    ) == (200, True, 0, 1)


def test_saving_climate_settings_requires_login(f_anonymous_client: Client, m_update_use_case: MagicMock) -> None:
    response = f_anonymous_client.post('/settings/climate/', POSTED)

    assert (response.status_code, response['Location'], m_update_use_case.init.call_count) == (
        302,
        '/login/?next=/settings/climate/',
        0,
    )
