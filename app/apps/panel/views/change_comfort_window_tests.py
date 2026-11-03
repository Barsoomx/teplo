from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, time
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.db import IntegrityError, OperationalError
from django.test import Client

from apps.climate.domain.errors import ComfortWindowNotFoundError
from apps.climate.usecases.change_comfort_window import ChangeComfortWindowInputDTO, ChangeComfortWindowOutputDTO
from apps.core.domain.errors import SettingsRejectedError
from apps.core.domain.settings import SettingProblem
from apps.panel.usecases.get_schedule_page import GetSchedulePageOutputDTO

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
NOT_SAVED = 'Не сохранено: база данных не приняла изменение. Попробуйте ещё раз'


@pytest.fixture
def m_change_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.change_comfort_window.ChangeComfortWindowUseCase') as use_case:
        use_case.init.return_value.execute.return_value = ChangeComfortWindowOutputDTO(zone='kitchen', changes=())
        yield use_case


@pytest.fixture
def m_backwards_change_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.change_comfort_window.ChangeComfortWindowUseCase') as use_case:
        use_case.init.return_value.execute.side_effect = SettingsRejectedError(
            (SettingProblem(field='end', code='not_after_start'),),
        )
        yield use_case


@pytest.fixture
def m_missing_window_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.change_comfort_window.ChangeComfortWindowUseCase') as use_case:
        use_case.init.return_value.execute.side_effect = ComfortWindowNotFoundError(99)
        yield use_case


@pytest.fixture
def m_change_use_case_the_database_refuses(request: pytest.FixtureRequest) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.change_comfort_window.ChangeComfortWindowUseCase') as use_case:
        use_case.init.return_value.execute.side_effect = request.param
        yield use_case


@pytest.fixture
def m_schedule_use_case(schedule_page: GetSchedulePageOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.change_comfort_window.GetSchedulePageUseCase') as use_case:
        use_case.init.return_value.execute.return_value = schedule_page
        yield use_case


def test_changed_window_goes_to_the_use_case_and_its_zone_is_drawn_again(
    f_client: Client,
    m_change_use_case: MagicMock,
    m_schedule_use_case: MagicMock,
) -> None:
    response = f_client.post('/schedule/windows/1/', {'start': '07:00', 'end': '23:00'})

    executed = m_change_use_case.init.return_value.execute.call_args.args[0]
    content = response.content.decode()
    assert (
        response.status_code,
        replace(executed, now=NOW),
        'id="schedule-kitchen"' in content,
        '<details class="window" open>' in content,
        m_schedule_use_case.init.call_count,
    ) == (
        200,
        ChangeComfortWindowInputDTO(window=1, start=time(7, 0), end=time(23, 0), now=NOW),
        True,
        False,
        1,
    )


def test_window_that_ends_before_it_starts_is_explained_in_the_open_window(
    f_russian_client: Client,
    m_backwards_change_use_case: MagicMock,
    m_schedule_use_case: MagicMock,
) -> None:
    response = f_russian_client.post('/schedule/windows/1/', {'start': '07:00', 'end': '06:00'})

    content = response.content.decode()
    assert (
        response.status_code,
        'Окно должно заканчиваться позже начала, в тот же день' in content,
        '<details class="window" open>' in content,
        ('value="07:00"' in content, 'value="06:00"' in content),
        m_backwards_change_use_case.init.call_count,
        m_schedule_use_case.init.call_count,
    ) == (200, True, True, (True, True), 1, 1)


def test_garbled_time_is_refused_before_the_use_case(
    f_client: Client,
    m_change_use_case: MagicMock,
    m_schedule_use_case: MagicMock,
) -> None:
    response = f_client.post('/schedule/windows/1/', {'start': 'soon', 'end': '06:00'})

    assert (
        response.status_code,
        'Enter a valid time.' in response.content.decode(),
        m_change_use_case.init.call_count,
        m_schedule_use_case.init.call_count,
    ) == (200, True, 0, 1)


def test_unknown_window_returns_404(
    f_client: Client,
    m_missing_window_use_case: MagicMock,
    m_schedule_use_case: MagicMock,
) -> None:
    response = f_client.post('/schedule/windows/99/', {'start': '07:00', 'end': '23:00'})

    assert (
        response.status_code,
        m_missing_window_use_case.init.call_count,
        m_schedule_use_case.init.call_count,
    ) == (404, 1, 0)


def test_changing_a_window_requires_login(f_anonymous_client: Client, m_change_use_case: MagicMock) -> None:
    response = f_anonymous_client.post('/schedule/windows/1/', {'start': '07:00', 'end': '23:00'})

    assert (response.status_code, response['Location'], m_change_use_case.init.call_count) == (
        302,
        '/login/?next=/schedule/windows/1/',
        0,
    )


@pytest.mark.parametrize(
    'm_change_use_case_the_database_refuses',
    [OperationalError('database is locked'), IntegrityError('CHECK constraint failed')],
    ids=['database_locked', 'check_failed'],
    indirect=True,
)
def test_window_change_the_database_does_not_take_is_answered_in_the_open_window_with_what_was_typed(
    f_russian_client: Client,
    m_change_use_case_the_database_refuses: MagicMock,
    m_schedule_use_case: MagicMock,
) -> None:
    response = f_russian_client.post('/schedule/windows/1/', {'start': '07:00', 'end': '23:00'})

    content = response.content.decode()
    assert (
        response.status_code,
        content.count(NOT_SAVED),
        '<details class="window" open>' in content,
        ('value="07:00"' in content, 'value="23:00"' in content),
        m_change_use_case_the_database_refuses.init.call_count,
        m_schedule_use_case.init.call_count,
    ) == (200, 1, True, (True, True), 1, 1)
