from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.db import IntegrityError, OperationalError
from django.test import Client

from apps.climate.domain.errors import ZoneNotFoundError
from apps.climate.usecases.copy_comfort_day import CopyComfortDayInputDTO, CopyComfortDayOutputDTO
from apps.core.domain.errors import SettingsRejectedError
from apps.core.domain.settings import SettingProblem
from apps.panel.usecases.get_schedule_page import GetSchedulePageOutputDTO

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
URL = '/schedule/kitchen/days/0/copy/'
NOT_SAVED = 'Не сохранено: база данных не приняла изменение. Попробуйте ещё раз'


@pytest.fixture
def m_copy_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.copy_comfort_day.CopyComfortDayUseCase') as use_case:
        use_case.init.return_value.execute.return_value = CopyComfortDayOutputDTO(zone='kitchen', changes=())
        yield use_case


@pytest.fixture
def m_off_grid_copy_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.copy_comfort_day.CopyComfortDayUseCase') as use_case:
        use_case.init.return_value.execute.side_effect = SettingsRejectedError(
            (SettingProblem(field='end', code='off_grid'),),
        )
        yield use_case


@pytest.fixture
def m_missing_zone_copy_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.copy_comfort_day.CopyComfortDayUseCase') as use_case:
        use_case.init.return_value.execute.side_effect = ZoneNotFoundError('attic')
        yield use_case


@pytest.fixture
def m_copy_use_case_the_database_refuses(request: pytest.FixtureRequest) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.copy_comfort_day.CopyComfortDayUseCase') as use_case:
        use_case.init.return_value.execute.side_effect = request.param
        yield use_case


@pytest.fixture
def m_schedule_use_case(schedule_page: GetSchedulePageOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.copy_comfort_day.GetSchedulePageUseCase') as use_case:
        use_case.init.return_value.execute.return_value = schedule_page
        yield use_case


def test_day_copied_to_the_picked_days_goes_to_the_use_case(
    f_client: Client,
    m_copy_use_case: MagicMock,
    m_schedule_use_case: MagicMock,
) -> None:
    response = f_client.post(URL, {'weekdays': ['1', '4']})

    executed = m_copy_use_case.init.return_value.execute.call_args.args[0]
    assert (
        response.status_code,
        replace(executed, now=NOW),
        'id="schedule-kitchen"' in response.content.decode(),
        m_schedule_use_case.init.call_count,
    ) == (200, CopyComfortDayInputDTO(zone='kitchen', weekday=0, to_weekdays=(1, 4), now=NOW), True, 1)


def test_copy_without_a_picked_day_is_explained_before_the_use_case(
    f_russian_client: Client,
    m_copy_use_case: MagicMock,
    m_schedule_use_case: MagicMock,
) -> None:
    response = f_russian_client.post(URL, {})

    content = response.content.decode()
    assert (
        response.status_code,
        'Выберите хотя бы один день' in content,
        content.count('<details class="day-tool" open>'),
        m_copy_use_case.init.call_count,
        m_schedule_use_case.init.call_count,
    ) == (200, True, 1, 0, 1)


def test_day_with_a_window_off_the_grid_is_explained_with_the_days_kept_picked(
    f_russian_client: Client,
    m_off_grid_copy_use_case: MagicMock,
    m_schedule_use_case: MagicMock,
) -> None:
    response = f_russian_client.post(URL, {'weekdays': ['2']})

    content = response.content.decode()
    assert (
        response.status_code,
        'Время с шагом 15 минут: :00, :15, :30 или :45' in content,
        'name="weekdays" value="2" checked' in content,
        'name="weekdays" value="3" checked' in content,
        m_off_grid_copy_use_case.init.call_count,
        m_schedule_use_case.init.call_count,
    ) == (200, True, True, False, 1, 1)


def test_copy_from_an_unknown_zone_returns_404(
    f_client: Client,
    m_missing_zone_copy_use_case: MagicMock,
    m_schedule_use_case: MagicMock,
) -> None:
    response = f_client.post('/schedule/attic/days/0/copy/', {'weekdays': ['1']})

    assert (
        response.status_code,
        m_missing_zone_copy_use_case.init.call_count,
        m_schedule_use_case.init.call_count,
    ) == (404, 1, 0)


def test_copying_a_day_requires_login(f_anonymous_client: Client, m_copy_use_case: MagicMock) -> None:
    response = f_anonymous_client.post(URL, {'weekdays': ['1']})

    assert (response.status_code, response['Location'], m_copy_use_case.init.call_count) == (
        302,
        f'/login/?next={URL}',
        0,
    )


@pytest.mark.parametrize(
    'm_copy_use_case_the_database_refuses',
    [OperationalError('database is locked'), IntegrityError('CHECK constraint failed')],
    ids=['database_locked', 'check_failed'],
    indirect=True,
)
def test_day_copy_the_database_does_not_take_is_answered_in_its_day_with_the_days_kept_picked(
    f_russian_client: Client,
    m_copy_use_case_the_database_refuses: MagicMock,
    m_schedule_use_case: MagicMock,
) -> None:
    response = f_russian_client.post(URL, {'weekdays': ['2']})

    content = response.content.decode()
    assert (
        response.status_code,
        content.count(NOT_SAVED),
        content.count('<details class="day-tool" open>'),
        'name="weekdays" value="2" checked' in content,
        m_copy_use_case_the_database_refuses.init.call_count,
        m_schedule_use_case.init.call_count,
    ) == (200, 1, 1, True, 1, 1)
