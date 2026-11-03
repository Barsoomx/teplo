from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.db import IntegrityError, OperationalError
from django.test import Client

from apps.climate.usecases.update_zone_settings import UpdateZoneSettingsInputDTO, UpdateZoneSettingsOutputDTO
from apps.core.domain.errors import SettingsRejectedError
from apps.core.domain.settings import SettingChange, SettingProblem
from apps.panel.usecases.get_settings_page import GetSettingsPageOutputDTO

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
POSTED = {
    'name': 'Кухня',
    'heating_enabled': 'on',
    'comfort_floor_setpoint': '27.5',
    'night_floor_setpoint': '22.0',
    'boost_floor_setpoint': '30.0',
    'idle_floor_setpoint': '18.0',
    'floor_limit_setpoint': '30.0',
    'comfort_air_target': '22.0',
    'night_air_target': '19.5',
    'preheat_lead_minutes': '150',
}
GATES = {'live_control': 'on', 'air_control_live': 'on'}
KITCHEN_EDIT = UpdateZoneSettingsInputDTO(
    zone='kitchen',
    name='Кухня',
    heating_enabled=True,
    comfort_floor_setpoint=Decimal('27.5'),
    night_floor_setpoint=Decimal('22.0'),
    boost_floor_setpoint=Decimal('30.0'),
    idle_floor_setpoint=Decimal('18.0'),
    floor_limit_setpoint=Decimal('30.0'),
    comfort_air_target=Decimal('22.0'),
    night_air_target=Decimal('19.5'),
    preheat_lead_minutes=150,
    now=NOW,
)
LOWERED = SettingChange(
    target='zone',
    subject='kitchen',
    name='Кухня',
    field='comfort_floor_setpoint',
    old='28.0',
    new='27.5',
)


@pytest.fixture
def m_settings_use_case(settings_page: GetSettingsPageOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.zone_settings.GetSettingsPageUseCase') as use_case:
        use_case.init.return_value.execute.return_value = settings_page
        yield use_case


@pytest.fixture
def m_update_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.zone_settings.UpdateZoneSettingsUseCase') as use_case:
        use_case.init.return_value.execute.return_value = UpdateZoneSettingsOutputDTO(changes=(LOWERED,))
        yield use_case


@pytest.fixture
def m_unchanged_update_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.zone_settings.UpdateZoneSettingsUseCase') as use_case:
        use_case.init.return_value.execute.return_value = UpdateZoneSettingsOutputDTO(changes=())
        yield use_case


@pytest.fixture
def m_rejecting_update_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.zone_settings.UpdateZoneSettingsUseCase') as use_case:
        use_case.init.return_value.execute.side_effect = SettingsRejectedError(
            (SettingProblem(field='night_floor_setpoint', code='above_comfort'),),
        )
        yield use_case


@pytest.fixture
def m_update_use_case_the_database_refuses(request: pytest.FixtureRequest) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.zone_settings.UpdateZoneSettingsUseCase') as use_case:
        use_case.init.return_value.execute.side_effect = request.param
        yield use_case


def test_saved_zone_settings_go_to_the_use_case_and_the_card_says_when_they_take_effect(
    f_russian_client: Client,
    m_settings_use_case: MagicMock,
    m_update_use_case: MagicMock,
) -> None:
    response = f_russian_client.post('/settings/zones/kitchen/', POSTED)

    executed = m_update_use_case.init.return_value.execute.call_args.args[0]
    content = response.content.decode()
    assert (
        response.status_code,
        replace(executed, now=NOW),
        'Сохранено. Термостат получит это при следующей разрешённой записи зоны.' in content,
        'id="zone-settings-kitchen"' in content,
        '<html' in content,
        m_settings_use_case.init.call_count,
    ) == (200, KITCHEN_EDIT, True, True, False, 2)


def test_zone_settings_as_stored_say_nothing_changed(
    f_russian_client: Client,
    m_settings_use_case: MagicMock,
    m_unchanged_update_use_case: MagicMock,
) -> None:
    response = f_russian_client.post('/settings/zones/kitchen/', POSTED)

    assert (
        'Ничего не изменилось' in response.content.decode(),
        m_unchanged_update_use_case.init.call_count,
        m_settings_use_case.init.call_count,
    ) == (True, 1, 2)


def test_posted_gates_never_reach_the_use_case(
    f_client: Client,
    m_settings_use_case: MagicMock,
    m_update_use_case: MagicMock,
) -> None:
    f_client.post('/settings/zones/kitchen/', {**POSTED, **GATES})

    executed = m_update_use_case.init.return_value.execute.call_args.args[0]
    assert (
        replace(executed, now=NOW),
        [gate for gate in GATES if hasattr(executed, gate)],
        m_settings_use_case.init.call_count,
    ) == (KITCHEN_EDIT, [], 2)


def test_floor_zone_is_saved_without_air_targets(
    f_client: Client,
    m_settings_use_case: MagicMock,
    m_update_use_case: MagicMock,
) -> None:
    posted = {
        field: value for field, value in POSTED.items() if field not in {'comfort_air_target', 'night_air_target'}
    }

    f_client.post('/settings/zones/bathroom-1f/', {**posted, 'name': 'Санузел 1 этаж'})

    executed = m_update_use_case.init.return_value.execute.call_args.args[0]
    assert (
        (executed.zone, executed.name, executed.comfort_air_target, executed.night_air_target),
        m_settings_use_case.init.call_count,
    ) == (('bathroom-1f', 'Санузел 1 этаж', None, None), 2)


def test_settings_out_of_order_are_explained_at_their_field(
    f_russian_client: Client,
    m_settings_use_case: MagicMock,
    m_rejecting_update_use_case: MagicMock,
) -> None:
    response = f_russian_client.post('/settings/zones/kitchen/', {**POSTED, 'night_floor_setpoint': '29'})

    content = response.content.decode()
    assert (
        response.status_code,
        'Не может быть выше комфорта' in content,
        'name="night_floor_setpoint" value="29"' in content,
        'Сохранено' in content,
        m_rejecting_update_use_case.init.call_count,
        m_settings_use_case.init.call_count,
    ) == (200, True, True, False, 1, 1)


def test_setpoint_the_thermostat_would_not_take_is_refused_before_the_use_case(
    f_russian_client: Client,
    m_settings_use_case: MagicMock,
    m_update_use_case: MagicMock,
) -> None:
    response = f_russian_client.post('/settings/zones/kitchen/', {**POSTED, 'comfort_floor_setpoint': '45'})

    assert (
        response.status_code,
        'Убедитесь, что это значение меньше либо равно 40.0.' in response.content.decode(),
        m_update_use_case.init.call_count,
        m_settings_use_case.init.call_count,
    ) == (200, True, 0, 1)


def test_settings_of_an_unknown_zone_return_404(
    f_client: Client,
    m_settings_use_case: MagicMock,
    m_update_use_case: MagicMock,
) -> None:
    response = f_client.post('/settings/zones/attic/', POSTED)

    assert (response.status_code, m_update_use_case.init.call_count, m_settings_use_case.init.call_count) == (
        404,
        0,
        1,
    )


def test_saving_zone_settings_requires_login(f_anonymous_client: Client, m_update_use_case: MagicMock) -> None:
    response = f_anonymous_client.post('/settings/zones/kitchen/', POSTED)

    assert (response.status_code, response['Location'], m_update_use_case.init.call_count) == (
        302,
        '/login/?next=/settings/zones/kitchen/',
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
    response = f_russian_client.post('/settings/zones/kitchen/', POSTED)

    content = response.content.decode()
    assert (
        response.status_code,
        'Не сохранено: база данных не приняла изменение. Попробуйте ещё раз' in content,
        'name="comfort_floor_setpoint" value="27.5"' in content,
        'Сохранено' in content,
        m_update_use_case_the_database_refuses.init.call_count,
        m_settings_use_case.init.call_count,
    ) == (200, True, True, False, 1, 1)
