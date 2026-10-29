from collections.abc import Iterator
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.test import Client

from apps.panel.usecases.get_panel_overview import GetPanelOverviewOutputDTO

pytestmark = pytest.mark.django_db


@pytest.fixture
def m_overview_use_case(panel_overview: GetPanelOverviewOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.dashboard.GetPanelOverviewUseCase') as use_case:
        use_case.init.return_value.execute.return_value = panel_overview
        yield use_case


def test_dashboard_lists_zones(f_client: Client, m_overview_use_case: MagicMock) -> None:
    response = f_client.get('/')

    assert (response.status_code, 'Кухня' in response.content.decode(), m_overview_use_case.init.call_count) == (
        200,
        True,
        1,
    )


def test_dashboard_redirects_anonymous_to_login(f_anonymous_client: Client) -> None:
    response = f_anonymous_client.get('/')

    assert (response.status_code, response['Location']) == (302, '/login/?next=/')
