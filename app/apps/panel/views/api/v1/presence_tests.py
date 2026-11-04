from collections.abc import Iterator
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.test import Client

from apps.panel.usecases.get_panel_overview import GetPanelOverviewOutputDTO

pytestmark = pytest.mark.django_db


@pytest.fixture
def m_overview_use_case(occupancy_overview: GetPanelOverviewOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.api.v1.presence.GetPanelOverviewUseCase') as use_case:
        use_case.init.return_value.execute.return_value = occupancy_overview
        yield use_case


def test_presence_api_lists_the_presence_sources_with_their_node_and_last_report(
    f_client: Client,
    m_overview_use_case: MagicMock,
) -> None:
    response = f_client.get('/api/v1/presence/')

    assert (response.status_code, response.json(), m_overview_use_case.init.call_count) == (
        200,
        [
            {
                'slug': 'pc-office',
                'name': 'Компьютер: кабинет',
                'room': 'office',
                'node': 'teplo-pc-office',
                'available': True,
                'present': False,
                'age_seconds': 12,
                'seen_age_seconds': 300,
                'error_code': None,
            },
            {
                'slug': 'presence-office',
                'name': 'Радар: кабинет',
                'room': 'office',
                'node': 'teplo-air-office',
                'available': False,
                'present': None,
                'age_seconds': None,
                'seen_age_seconds': None,
                'error_code': 'broker_unavailable',
            },
        ],
        1,
    )


def test_presence_api_requires_login(f_anonymous_client: Client) -> None:
    assert f_anonymous_client.get('/api/v1/presence/').status_code == 403
