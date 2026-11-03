from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.db import IntegrityError, OperationalError
from django.test import Client

from apps.climate.usecases.update_room_climate import UpdateRoomClimateInputDTO, UpdateRoomClimateOutputDTO
from apps.core.domain.errors import SettingsRejectedError
from apps.core.domain.settings import SettingChange, SettingProblem
from apps.panel.usecases.get_settings_page import GetSettingsPageOutputDTO

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
URL = '/settings/rooms/bedroom/'
POSTED = {
    'comfort_target': '21.0',
    'setback_target': '19.5',
    'backup_below': '19.0',
    'backup_hysteresis': '0.30',
}
BEDROOM_EDIT = UpdateRoomClimateInputDTO(
    room='bedroom',
    comfort_target=Decimal('21.0'),
    setback_target=Decimal('19.5'),
    backup_below=Decimal('19.0'),
    backup_hysteresis=Decimal('0.30'),
    now=NOW,
)
RAISED = SettingChange(target='room', subject='bedroom', name='Спальня', field='comfort_target', old='20.5', new='21.0')
NOT_SAVED = 'Не сохранено: база данных не приняла изменение. Попробуйте ещё раз'


@pytest.fixture
def m_settings_use_case(settings_page: GetSettingsPageOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.room_settings.GetSettingsPageUseCase') as use_case:
        use_case.init.return_value.execute.return_value = settings_page
        yield use_case


@pytest.fixture
def m_update_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.room_settings.UpdateRoomClimateUseCase') as use_case:
        use_case.init.return_value.execute.return_value = UpdateRoomClimateOutputDTO(changes=(RAISED,))
        yield use_case


@pytest.fixture
def m_unchanged_update_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.room_settings.UpdateRoomClimateUseCase') as use_case:
        use_case.init.return_value.execute.return_value = UpdateRoomClimateOutputDTO(changes=())
        yield use_case


@pytest.fixture
def m_rejecting_update_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.room_settings.UpdateRoomClimateUseCase') as use_case:
        use_case.init.return_value.execute.side_effect = SettingsRejectedError(
            (SettingProblem(field='backup_below', code='not_below_setback'),),
        )
        yield use_case


@pytest.fixture
def m_update_use_case_the_database_refuses(request: pytest.FixtureRequest) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.room_settings.UpdateRoomClimateUseCase') as use_case:
        use_case.init.return_value.execute.side_effect = request.param
        yield use_case


def test_saved_room_settings_go_to_the_use_case_and_the_card_says_when_they_take_effect(
    f_russian_client: Client,
    m_settings_use_case: MagicMock,
    m_update_use_case: MagicMock,
) -> None:
    response = f_russian_client.post(URL, POSTED)

    executed = m_update_use_case.init.return_value.execute.call_args.args[0]
    content = response.content.decode()
    assert (
        response.status_code,
        replace(executed, now=NOW),
        'Сохранено. Цикл отопления учтёт это в течение минуты.' in content,
        'id="room-settings-bedroom"' in content,
        '<html' in content,
        m_settings_use_case.init.call_count,
    ) == (200, BEDROOM_EDIT, True, True, False, 2)


def test_room_settings_as_stored_say_nothing_changed(
    f_russian_client: Client,
    m_settings_use_case: MagicMock,
    m_unchanged_update_use_case: MagicMock,
) -> None:
    response = f_russian_client.post(URL, POSTED)

    assert (
        'Ничего не изменилось' in response.content.decode(),
        m_unchanged_update_use_case.init.call_count,
        m_settings_use_case.init.call_count,
    ) == (True, 1, 2)


def test_room_without_backup_heat_is_saved_without_backup_values(
    f_client: Client,
    m_settings_use_case: MagicMock,
    m_update_use_case: MagicMock,
) -> None:
    f_client.post('/settings/rooms/bathroom-1f/', {**POSTED, 'backup_below': 'warm'})

    executed = m_update_use_case.init.return_value.execute.call_args.args[0]
    assert (
        (executed.room, executed.comfort_target, executed.backup_below, executed.backup_hysteresis),
        m_settings_use_case.init.call_count,
    ) == (('bathroom-1f', Decimal('21.0'), None, None), 2)


def test_posted_backup_gate_never_reaches_the_use_case(
    f_client: Client,
    m_settings_use_case: MagicMock,
    m_update_use_case: MagicMock,
) -> None:
    f_client.post(URL, {**POSTED, 'backup_live': 'on'})

    executed = m_update_use_case.init.return_value.execute.call_args.args[0]
    assert (replace(executed, now=NOW), hasattr(executed, 'backup_live'), m_settings_use_case.init.call_count) == (
        BEDROOM_EDIT,
        False,
        2,
    )


def test_backup_heat_too_close_to_the_night_target_is_explained_at_its_field(
    f_russian_client: Client,
    m_settings_use_case: MagicMock,
    m_rejecting_update_use_case: MagicMock,
) -> None:
    response = f_russian_client.post(URL, {**POSTED, 'backup_below': '19.3'})

    content = response.content.decode()
    assert (
        response.status_code,
        response.context['room_form'].errors,
        'name="backup_below" value="19.3"' in content,
        'Сохранено' in content,
        m_rejecting_update_use_case.init.call_count,
        m_settings_use_case.init.call_count,
    ) == (
        200,
        {'backup_below': ['Должно быть хотя бы на 0,5° ниже ночной цели, иначе резерв спорит с ней']},
        True,
        False,
        1,
        1,
    )


def test_target_out_of_range_is_refused_before_the_use_case(
    f_russian_client: Client,
    m_settings_use_case: MagicMock,
    m_update_use_case: MagicMock,
) -> None:
    response = f_russian_client.post(URL, {**POSTED, 'comfort_target': '45'})

    assert (
        response.status_code,
        'Убедитесь, что это значение меньше либо равно 40.0.' in response.content.decode(),
        m_update_use_case.init.call_count,
        m_settings_use_case.init.call_count,
    ) == (200, True, 0, 1)


def test_settings_of_a_room_without_room_climate_return_404(
    f_client: Client,
    m_settings_use_case: MagicMock,
    m_update_use_case: MagicMock,
) -> None:
    response = f_client.post('/settings/rooms/office/', POSTED)

    assert (response.status_code, m_update_use_case.init.call_count, m_settings_use_case.init.call_count) == (
        404,
        0,
        1,
    )


def test_saving_room_settings_requires_login(f_anonymous_client: Client, m_update_use_case: MagicMock) -> None:
    response = f_anonymous_client.post(URL, POSTED)

    assert (response.status_code, response['Location'], m_update_use_case.init.call_count) == (
        302,
        f'/login/?next={URL}',
        0,
    )


@pytest.mark.parametrize(
    'm_update_use_case_the_database_refuses',
    [OperationalError('database is locked'), IntegrityError('CHECK constraint failed')],
    ids=['database_locked', 'check_failed'],
    indirect=True,
)
def test_room_save_the_database_does_not_take_is_answered_on_the_card_with_what_was_typed(
    f_russian_client: Client,
    m_settings_use_case: MagicMock,
    m_update_use_case_the_database_refuses: MagicMock,
) -> None:
    response = f_russian_client.post(URL, {**POSTED, 'comfort_target': '21.5'})

    content = response.content.decode()
    assert (
        response.status_code,
        content.count(NOT_SAVED),
        'name="comfort_target" value="21.5"' in content,
        'Сохранено' in content,
        m_update_use_case_the_database_refuses.init.call_count,
        m_settings_use_case.init.call_count,
    ) == (200, 1, True, False, 1, 1)
