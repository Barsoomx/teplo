from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.db import IntegrityError, OperationalError
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
    'air_min_band': '0.20',
    'learned_mornings_needed': '5',
    'preheat_lead_cap_minutes': '210',
    'guests_end_hour': '12',
}
DEAD_GUARD = {
    'second_floor_guard_threshold': '18.5',
    'second_floor_guard_hysteresis': '0.50',
    'second_floor_guard_live': 'on',
}
BELOW_MIN_BAND = 'Не может быть меньше минимального зазора, иначе обогрев остановится выше цели'


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


@pytest.fixture
def m_band_rejecting_update_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.climate_settings.UpdateClimateSettingsUseCase') as use_case:
        use_case.init.return_value.execute.side_effect = SettingsRejectedError(
            (SettingProblem(field='air_start_offset', code='below_min_band'),),
        )
        yield use_case


@pytest.fixture
def m_update_use_case_the_database_refuses(request: pytest.FixtureRequest) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.climate_settings.UpdateClimateSettingsUseCase') as use_case:
        use_case.init.return_value.execute.side_effect = request.param
        yield use_case


def test_saved_climate_settings_go_to_the_use_case_without_the_old_second_floor_guard(
    f_russian_client: Client,
    m_settings_use_case: MagicMock,
    m_update_use_case: MagicMock,
) -> None:
    response = f_russian_client.post('/settings/climate/', {**POSTED, **DEAD_GUARD})

    executed = m_update_use_case.init.return_value.execute.call_args.args[0]
    content = response.content.decode()
    assert (
        response.status_code,
        replace(executed, now=NOW),
        [field for field in DEAD_GUARD if hasattr(executed, field)],
        'Сохранено. Цикл отопления учтёт это в течение минуты.' in content,
        'id="climate-settings"' in content,
        ('second_floor_guard' in content, 'Защита второго этажа' in content, 'class="gates"' in content),
        m_settings_use_case.init.call_count,
    ) == (
        200,
        UpdateClimateSettingsInputDTO(
            air_start_offset=Decimal('0.25'),
            initial_early_stop_offset=Decimal('0.30'),
            air_min_band=Decimal('0.20'),
            learned_mornings_needed=5,
            preheat_lead_cap_minutes=210,
            guests_end_hour=12,
            now=NOW,
        ),
        [],
        True,
        True,
        (False, False, False),
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
        response.context['climate_form'].errors,
        '<span class="field-error">Вне допустимого диапазона</span>' in content,
        'name="preheat_lead_cap_minutes" value="210"' in content,
        m_rejecting_update_use_case.init.call_count,
        m_settings_use_case.init.call_count,
    ) == (200, {'preheat_lead_cap_minutes': ['Вне допустимого диапазона']}, True, True, 1, 1)


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


@pytest.mark.parametrize('hour', ['24', '-1', ''], ids=['past_the_day', 'before_midnight', 'missing'])
def test_guests_end_hour_outside_the_day_is_refused_before_the_use_case(
    f_russian_client: Client,
    m_settings_use_case: MagicMock,
    m_update_use_case: MagicMock,
    hour: str,
) -> None:
    response = f_russian_client.post('/settings/climate/', {**POSTED, 'guests_end_hour': hour})

    assert (
        response.status_code,
        list(response.context['climate_form'].errors),
        'id="climate-guests_end_hour"' in response.content.decode(),
        m_update_use_case.init.call_count,
        m_settings_use_case.init.call_count,
    ) == (200, ['guests_end_hour'], True, 0, 1)


def test_start_offset_below_the_min_band_is_refused_at_its_field_before_the_use_case(
    f_russian_client: Client,
    m_settings_use_case: MagicMock,
    m_update_use_case: MagicMock,
) -> None:
    response = f_russian_client.post('/settings/climate/', {**POSTED, 'air_start_offset': '0.15'})

    content = response.content.decode()
    assert (
        response.status_code,
        response.context['climate_form'].errors,
        f'<span class="field-error">{BELOW_MIN_BAND}</span>' in content,
        'name="air_start_offset" value="0.15"' in content,
        m_update_use_case.init.call_count,
        m_settings_use_case.init.call_count,
    ) == (200, {'air_start_offset': [BELOW_MIN_BAND]}, True, True, 0, 1)


def test_start_offset_the_use_case_finds_below_the_min_band_is_explained_at_its_field(
    f_russian_client: Client,
    m_settings_use_case: MagicMock,
    m_band_rejecting_update_use_case: MagicMock,
) -> None:
    response = f_russian_client.post('/settings/climate/', POSTED)

    content = response.content.decode()
    assert (
        response.status_code,
        response.context['climate_form'].errors,
        f'<span class="field-error">{BELOW_MIN_BAND}</span>' in content,
        'name="air_start_offset" value="0.25"' in content,
        m_band_rejecting_update_use_case.init.call_count,
        m_settings_use_case.init.call_count,
    ) == (200, {'air_start_offset': [BELOW_MIN_BAND]}, True, True, 1, 1)


def test_saving_climate_settings_requires_login(f_anonymous_client: Client, m_update_use_case: MagicMock) -> None:
    response = f_anonymous_client.post('/settings/climate/', POSTED)

    assert (response.status_code, response['Location'], m_update_use_case.init.call_count) == (
        302,
        '/login/?next=/settings/climate/',
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
    response = f_russian_client.post('/settings/climate/', POSTED)

    content = response.content.decode()
    assert (
        response.status_code,
        'Не сохранено: база данных не приняла изменение. Попробуйте ещё раз' in content,
        'name="preheat_lead_cap_minutes" value="210"' in content,
        'Сохранено' in content,
        m_update_use_case_the_database_refuses.init.call_count,
        m_settings_use_case.init.call_count,
    ) == (200, True, True, False, 1, 1)
