from collections.abc import Iterator
from datetime import UTC, datetime
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.test import Client
from freezegun import freeze_time

from apps.climate.domain.modes import CommandSource
from apps.climate.usecases.clear_room_override import ClearRoomOverrideInputDTO
from apps.house.domain.errors import RoomNotFoundError
from apps.panel.usecases.get_panel_overview import GetPanelOverviewOutputDTO

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 4, 16, 45, tzinfo=UTC)
URL = '/rooms/open-space/override/clear/'


@pytest.fixture
def m_clear_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.clear_room_override.ClearRoomOverrideUseCase') as use_case:
        yield use_case


@pytest.fixture
def m_missing_room_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.clear_room_override.ClearRoomOverrideUseCase') as use_case:
        use_case.init.return_value.execute.side_effect = RoomNotFoundError('attic')
        yield use_case


@pytest.fixture
def m_overview_use_case(panel_overview: GetPanelOverviewOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.clear_room_override.GetPanelOverviewUseCase') as use_case:
        use_case.init.return_value.execute.return_value = panel_overview
        yield use_case


@freeze_time(NOW)
def test_back_to_the_schedule_goes_to_the_use_case_from_the_panel_and_the_dashboard_refreshes(
    f_client: Client,
    m_clear_use_case: MagicMock,
    m_overview_use_case: MagicMock,
) -> None:
    response = f_client.post(URL)

    assert (
        response.status_code,
        m_clear_use_case.init.return_value.execute.call_args.args,
        'Кухня' in response.content.decode(),
        m_overview_use_case.init.call_count,
    ) == (200, (ClearRoomOverrideInputDTO(room='open-space', now=NOW, source=CommandSource.PANEL),), True, 1)


def test_back_to_the_schedule_for_an_unknown_room_returns_404(
    f_client: Client,
    m_missing_room_use_case: MagicMock,
    m_overview_use_case: MagicMock,
) -> None:
    response = f_client.post('/rooms/attic/override/clear/')

    assert (response.status_code, m_missing_room_use_case.init.call_count, m_overview_use_case.init.call_count) == (
        404,
        1,
        0,
    )


def test_back_to_the_schedule_requires_login(f_anonymous_client: Client, m_clear_use_case: MagicMock) -> None:
    response = f_anonymous_client.post(URL)

    assert (response.status_code, response['Location'], m_clear_use_case.init.call_count) == (
        302,
        f'/login/?next={URL}',
        0,
    )
