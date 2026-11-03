from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.test import Client

from apps.climate.domain.errors import ComfortWindowNotFoundError
from apps.climate.usecases.delete_comfort_window import DeleteComfortWindowInputDTO, DeleteComfortWindowOutputDTO
from apps.panel.usecases.get_schedule_page import GetSchedulePageOutputDTO

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)


@pytest.fixture
def m_delete_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.delete_comfort_window.DeleteComfortWindowUseCase') as use_case:
        use_case.init.return_value.execute.return_value = DeleteComfortWindowOutputDTO(zone='kitchen', changes=())
        yield use_case


@pytest.fixture
def m_missing_window_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.delete_comfort_window.DeleteComfortWindowUseCase') as use_case:
        use_case.init.return_value.execute.side_effect = ComfortWindowNotFoundError(99)
        yield use_case


@pytest.fixture
def m_schedule_use_case(schedule_page: GetSchedulePageOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.delete_comfort_window.GetSchedulePageUseCase') as use_case:
        use_case.init.return_value.execute.return_value = schedule_page
        yield use_case


def test_deleted_window_goes_to_the_use_case_and_its_zone_is_drawn_again(
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
