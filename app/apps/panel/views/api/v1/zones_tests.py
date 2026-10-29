from collections.abc import Iterator
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.test import Client

from apps.panel.usecases.get_panel_overview import GetPanelOverviewOutputDTO

pytestmark = pytest.mark.django_db


@pytest.fixture
def m_overview_use_case(panel_overview: GetPanelOverviewOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.api.v1.zones.GetPanelOverviewUseCase') as use_case:
        use_case.init.return_value.execute.return_value = panel_overview
        yield use_case


def test_zones_api_returns_overview(f_client: Client, m_overview_use_case: MagicMock) -> None:
    response = f_client.get('/api/v1/zones/')

    assert (response.status_code, response.json(), m_overview_use_case.init.call_count) == (
        200,
        [
            {
                'slug': 'kitchen',
                'name': 'Кухня',
                'kind': 'air',
                'mode': 'comfort',
                'available': True,
                'error_code': None,
                'error': None,
                'floor_temperature': '24.5',
                'power': True,
                'setpoint': '28.0',
                'target_setpoint': '28.0',
                'boosted': False,
                'live_control': False,
            },
        ],
        1,
    )


def test_zones_api_requires_login(f_anonymous_client: Client) -> None:
    assert f_anonymous_client.get('/api/v1/zones/').status_code == 403
