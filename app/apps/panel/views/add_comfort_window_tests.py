from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, time
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.test import Client

from apps.climate.domain.errors import ZoneNotFoundError
from apps.climate.usecases.add_comfort_window import AddComfortWindowInputDTO, AddComfortWindowOutputDTO
from apps.core.domain.errors import SettingsRejectedError
from apps.core.domain.settings import SettingProblem
from apps.panel.usecases.get_schedule_page import GetSchedulePageOutputDTO

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
URL = '/schedule/kitchen/days/0/windows/'


@pytest.fixture
def m_add_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.add_comfort_window.AddComfortWindowUseCase') as use_case:
        use_case.init.return_value.execute.return_value = AddComfortWindowOutputDTO(zone='kitchen', changes=())
        yield use_case


@pytest.fixture
def m_overlap_add_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.add_comfort_window.AddComfortWindowUseCase') as use_case:
        use_case.init.return_value.execute.side_effect = SettingsRejectedError(
            (SettingProblem(field='', code='overlap'),),
        )
        yield use_case


@pytest.fixture
def m_missing_zone_add_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.add_comfort_window.AddComfortWindowUseCase') as use_case:
        use_case.init.return_value.execute.side_effect = ZoneNotFoundError('attic')
        yield use_case


@pytest.fixture
def m_schedule_use_case(schedule_page: GetSchedulePageOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.add_comfort_window.GetSchedulePageUseCase') as use_case:
        use_case.init.return_value.execute.return_value = schedule_page
        yield use_case


def test_added_window_goes_to_the_use_case_and_the_zone_is_drawn_again(
    f_client: Client,
    m_add_use_case: MagicMock,
    m_schedule_use_case: MagicMock,
) -> None:
    response = f_client.post(URL, {'start': '05:00', 'end': '06:00'})

    executed = m_add_use_case.init.return_value.execute.call_args.args[0]
    content = response.content.decode()
    assert (
        response.status_code,
        replace(executed, now=NOW),
        'id="schedule-kitchen"' in content,
        'id="schedule-living-room"' in content,
        '<html' in content,
        'form-error' in content,
        m_schedule_use_case.init.call_count,
    ) == (
        200,
        AddComfortWindowInputDTO(zone='kitchen', weekday=0, start=time(5, 0), end=time(6, 0), now=NOW),
        True,
        False,
        False,
        False,
        1,
    )


def test_window_off_the_quarter_hour_is_refused_before_the_use_case(
    f_russian_client: Client,
    m_add_use_case: MagicMock,
    m_schedule_use_case: MagicMock,
) -> None:
    response = f_russian_client.post(URL, {'start': '05:10', 'end': '06:00'})

    content = response.content.decode()
    assert (
        response.status_code,
        'Время с шагом 15 минут: :00, :15, :30 или :45' in content,
        '<details class="day-tool" open>' in content,
        'value="05:10"' in content,
        m_add_use_case.init.call_count,
        m_schedule_use_case.init.call_count,
    ) == (200, True, True, True, 0, 1)


def test_overlapping_window_is_explained_in_its_day(
    f_russian_client: Client,
    m_overlap_add_use_case: MagicMock,
    m_schedule_use_case: MagicMock,
) -> None:
    response = f_russian_client.post(URL, {'start': '23:00', 'end': '23:45'})

    content = response.content.decode()
    assert (
        response.status_code,
        content.count('Окна комфорта одного дня не должны пересекаться'),
        'value="23:00"' in content,
        m_overlap_add_use_case.init.call_count,
        m_schedule_use_case.init.call_count,
    ) == (200, 1, True, 1, 1)


def test_window_for_an_unknown_zone_returns_404(
    f_client: Client,
    m_missing_zone_add_use_case: MagicMock,
    m_schedule_use_case: MagicMock,
) -> None:
    response = f_client.post('/schedule/attic/days/0/windows/', {'start': '05:00', 'end': '06:00'})

    assert (response.status_code, m_missing_zone_add_use_case.init.call_count, m_schedule_use_case.init.call_count) == (
        404,
        1,
        0,
    )


def test_day_outside_the_week_returns_404(f_client: Client, m_add_use_case: MagicMock) -> None:
    response = f_client.post('/schedule/kitchen/days/7/windows/', {'start': '05:00', 'end': '06:00'})

    assert (response.status_code, m_add_use_case.init.call_count) == (404, 0)


def test_adding_a_window_requires_login(f_anonymous_client: Client, m_add_use_case: MagicMock) -> None:
    response = f_anonymous_client.post(URL, {'start': '05:00', 'end': '06:00'})

    assert (response.status_code, response['Location'], m_add_use_case.init.call_count) == (
        302,
        f'/login/?next={URL}',
        0,
    )
