from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.db import IntegrityError, OperationalError
from django.test import Client

from apps.climate.domain.errors import ComfortWindowNotFoundError
from apps.climate.usecases.delete_comfort_window import DeleteComfortWindowInputDTO, DeleteComfortWindowOutputDTO
from apps.panel.usecases.get_schedule_page import GetSchedulePageOutputDTO

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
NOT_SAVED = 'Не сохранено: база данных не приняла изменение. Попробуйте ещё раз'


@pytest.fixture
def m_delete_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.delete_comfort_window.DeleteComfortWindowUseCase') as use_case:
        use_case.init.return_value.execute.return_value = DeleteComfortWindowOutputDTO(room='kitchen', changes=())
        yield use_case


@pytest.fixture
def m_missing_window_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.delete_comfort_window.DeleteComfortWindowUseCase') as use_case:
        use_case.init.return_value.execute.side_effect = ComfortWindowNotFoundError(99)
        yield use_case


@pytest.fixture
def m_delete_use_case_the_database_refuses(request: pytest.FixtureRequest) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.delete_comfort_window.DeleteComfortWindowUseCase') as use_case:
        use_case.init.return_value.execute.side_effect = request.param
        yield use_case


@pytest.fixture
def m_schedule_use_case(schedule_page: GetSchedulePageOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.delete_comfort_window.GetSchedulePageUseCase') as use_case:
        use_case.init.return_value.execute.return_value = schedule_page
        yield use_case


def test_deleted_window_goes_to_the_use_case_and_its_room_is_drawn_again(
    f_client: Client,
    m_delete_use_case: MagicMock,
    m_schedule_use_case: MagicMock,
) -> None:
    response = f_client.post('/schedule/windows/1/delete/')

    executed = m_delete_use_case.init.return_value.execute.call_args.args[0]
    content = response.content.decode()
    assert (
        response.status_code,
        replace(executed, now=NOW),
        'id="schedule-kitchen"' in content,
        '<html' in content,
        m_schedule_use_case.init.call_count,
    ) == (200, DeleteComfortWindowInputDTO(window=1, now=NOW), True, False, 1)


def test_deleting_an_unknown_window_returns_404(
    f_client: Client,
    m_missing_window_use_case: MagicMock,
    m_schedule_use_case: MagicMock,
) -> None:
    response = f_client.post('/schedule/windows/99/delete/')

    assert (response.status_code, m_missing_window_use_case.init.call_count, m_schedule_use_case.init.call_count) == (
        404,
        1,
        0,
    )


def test_deleting_a_window_takes_a_post(f_client: Client, m_delete_use_case: MagicMock) -> None:
    response = f_client.get('/schedule/windows/1/delete/')

    assert (response.status_code, m_delete_use_case.init.call_count) == (405, 0)


def test_deleting_a_window_requires_login(f_anonymous_client: Client, m_delete_use_case: MagicMock) -> None:
    response = f_anonymous_client.post('/schedule/windows/1/delete/')

    assert (response.status_code, response['Location'], m_delete_use_case.init.call_count) == (
        302,
        '/login/?next=/schedule/windows/1/delete/',
        0,
    )


@pytest.mark.parametrize(
    'm_delete_use_case_the_database_refuses',
    [OperationalError('database is locked'), IntegrityError('CHECK constraint failed')],
    ids=['database_locked', 'check_failed'],
    indirect=True,
)
def test_window_the_database_does_not_delete_stays_open_in_its_room_with_the_reason(
    f_russian_client: Client,
    m_delete_use_case_the_database_refuses: MagicMock,
    m_schedule_use_case: MagicMock,
) -> None:
    response = f_russian_client.post('/schedule/windows/1/delete/')

    content = response.content.decode()
    assert (
        response.status_code,
        content.count(NOT_SAVED),
        'id="schedule-kitchen"' in content,
        '<details class="window" open>' in content,
        ('value="06:30"' in content, 'value="23:30"' in content),
        m_delete_use_case_the_database_refuses.init.call_count,
        m_schedule_use_case.init.call_count,
    ) == (200, 1, True, True, (True, True), 1, 1)
