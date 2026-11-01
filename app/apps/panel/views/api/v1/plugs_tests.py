from collections.abc import Iterator
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.test import Client

from apps.panel.usecases.get_panel_overview import GetPanelOverviewOutputDTO

pytestmark = pytest.mark.django_db


@pytest.fixture
def m_overview_use_case(panel_overview: GetPanelOverviewOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.api.v1.plugs.GetPanelOverviewUseCase') as use_case:
        use_case.init.return_value.execute.return_value = panel_overview
        yield use_case


def test_plugs_api_returns_plug_readings(f_client: Client, m_overview_use_case: MagicMock) -> None:
    response = f_client.get('/api/v1/plugs/')

    assert (response.status_code, response.json(), m_overview_use_case.init.call_count) == (
        200,
        [
            {
                'slug': 'plug-boiler-1f',
                'name': 'Бойлер 1 этаж',
                'available': True,
                'error_code': None,
                'error': None,
                'on': True,
                'power_watts': '1305.4',
                'current_amps': '6.020',
                'voltage_volts': '216.8',
            },
            {
                'slug': 'plug-boiler-2f',
                'name': 'Бойлер 2 этаж',
                'available': False,
                'error_code': 'device_protocol_error',
                'error': 'unexpected plug payload: {}',
                'on': None,
                'power_watts': None,
                'current_amps': None,
                'voltage_volts': None,
            },
        ],
        1,
    )


def test_plugs_api_requires_login(f_anonymous_client: Client) -> None:
    assert f_anonymous_client.get('/api/v1/plugs/').status_code == 403
