from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.test import Client

from apps.climate.domain.errors import ZoneNotFoundError
from apps.climate.usecases.copy_comfort_week import CopyComfortWeekInputDTO, CopyComfortWeekOutputDTO
from apps.core.domain.errors import SettingsRejectedError
from apps.core.domain.settings import SettingProblem
from apps.panel.usecases.get_schedule_page import GetSchedulePageOutputDTO

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
URL = '/schedule/kitchen/copy/'


@pytest.fixture
def m_copy_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.copy_comfort_week.CopyComfortWeekUseCase') as use_case:
        use_case.init.return_value.execute.return_value = CopyComfortWeekOutputDTO(zones=('living-room',), changes=())
        yield use_case


@pytest.fixture
def m_overlap_copy_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.copy_comfort_week.CopyComfortWeekUseCase') as use_case:
        use_case.init.return_value.execute.side_effect = SettingsRejectedError(
            (SettingProblem(field='', code='overlap'),),
        )
        yield use_case


@pytest.fixture
def m_missing_zone_copy_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.copy_comfort_week.CopyComfortWeekUseCase') as use_case:
        use_case.init.return_value.execute.side_effect = ZoneNotFoundError('attic')
        yield use_case


@pytest.fixture
def m_schedule_use_case(schedule_page: GetSchedulePageOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.copy_comfort_week.GetSchedulePageUseCase') as use_case:
        use_case.init.return_value.execute.return_value = schedule_page
        yield use_case


def test_week_copied_to_the_picked_zones_goes_to_the_use_case_and_every_zone_is_drawn_again(
    f_client: Client,
    m_copy_use_case: MagicMock,
    m_schedule_use_case: MagicMock,
) -> None:
    response = f_client.post(URL, {'zones': ['living-room']})

    executed = m_copy_use_case.init.return_value.execute.call_args.args[0]
    content = response.content.decode()
    assert (
        response.status_code,
        replace(executed, now=NOW),
        ['id="schedule-kitchen"' in content, 'id="schedule-living-room"' in content],
        m_schedule_use_case.init.call_count,
    ) == (200, CopyComfortWeekInputDTO(zone='kitchen', to_zones=('living-room',), now=NOW), [True, True], 1)


def test_copy_without_a_picked_zone_is_explained_in_the_source_zone(
    f_russian_client: Client,
    m_copy_use_case: MagicMock,
    m_schedule_use_case: MagicMock,
) -> None:
    response = f_russian_client.post(URL, {})

    content = response.content.decode()
    assert (
        response.status_code,
        content.count('Выберите хотя бы одну зону'),
        '<details class="week-tool" open>' in content,
        m_copy_use_case.init.call_count,
        m_schedule_use_case.init.call_count,
    ) == (200, 1, True, 0, 1)


def test_week_whose_windows_overlap_is_explained_with_the_zones_kept_picked(
    f_russian_client: Client,
    m_overlap_copy_use_case: MagicMock,
    m_schedule_use_case: MagicMock,
) -> None:
    response = f_russian_client.post(URL, {'zones': ['living-room']})

    content = response.content.decode()
    assert (
        response.status_code,
        'Окна комфорта одного дня не должны пересекаться' in content,
        'name="zones" value="living-room" checked' in content,
        m_overlap_copy_use_case.init.call_count,
        m_schedule_use_case.init.call_count,
    ) == (200, True, True, 1, 1)


def test_copy_to_an_unknown_zone_returns_404(
    f_client: Client,
    m_missing_zone_copy_use_case: MagicMock,
    m_schedule_use_case: MagicMock,
) -> None:
    response = f_client.post(URL, {'zones': ['attic']})

    assert (
        response.status_code,
        m_missing_zone_copy_use_case.init.call_count,
        m_schedule_use_case.init.call_count,
    ) == (404, 1, 0)


def test_copying_a_week_requires_login(f_anonymous_client: Client, m_copy_use_case: MagicMock) -> None:
    response = f_anonymous_client.post(URL, {'zones': ['living-room']})

    assert (response.status_code, response['Location'], m_copy_use_case.init.call_count) == (
        302,
        f'/login/?next={URL}',
        0,
    )
