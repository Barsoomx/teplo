from collections.abc import Iterator
from datetime import UTC, datetime
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.test import Client
from freezegun import freeze_time

from apps.climate.domain.modes import CommandSource
from apps.climate.usecases.set_guests import SetGuestsInputDTO
from apps.panel.usecases.get_panel_overview import GetPanelOverviewOutputDTO

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 4, 18, 0, tzinfo=UTC)


@pytest.fixture
def m_guests_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.set_guests.SetGuestsUseCase') as use_case:
        yield use_case


@pytest.fixture
def m_overview_use_case(panel_overview: GetPanelOverviewOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.set_guests.GetPanelOverviewUseCase') as use_case:
        use_case.init.return_value.execute.return_value = panel_overview
        yield use_case


@freeze_time(NOW)
@pytest.mark.parametrize('nights', [1, 0])
def test_guests_go_to_the_use_case_from_the_panel_and_the_dashboard_refreshes(
    f_client: Client,
    m_guests_use_case: MagicMock,
    m_overview_use_case: MagicMock,
    nights: int,
) -> None:
    response = f_client.post('/guests/', {'nights': nights})

    content = response.content.decode()
    assert (
        response.status_code,
        m_guests_use_case.init.return_value.execute.call_args.args,
        'Кухня' in content,
        '<html' in content,
        m_overview_use_case.init.call_count,
    ) == (200, (SetGuestsInputDTO(nights=nights, now=NOW, source=CommandSource.PANEL),), True, False, 1)


@pytest.mark.parametrize('posted', [{'nights': '4'}, {'nights': 'two'}, {}])
def test_guests_for_other_than_up_to_three_nights_are_refused(
    f_client: Client,
    m_guests_use_case: MagicMock,
    m_overview_use_case: MagicMock,
    posted: dict[str, str],
) -> None:
    response = f_client.post('/guests/', posted)

    assert (response.status_code, m_guests_use_case.init.call_count, m_overview_use_case.init.call_count) == (
        400,
        0,
        0,
    )


def test_setting_guests_requires_login(f_anonymous_client: Client, m_guests_use_case: MagicMock) -> None:
    response = f_anonymous_client.post('/guests/', {'nights': 1})

    assert (response.status_code, response['Location'], m_guests_use_case.init.call_count) == (
        302,
        '/login/?next=/guests/',
        0,
    )
