from collections.abc import Iterator
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.test import Client

from apps.panel.usecases.get_panel_overview import GetPanelOverviewOutputDTO

pytestmark = pytest.mark.django_db


@pytest.fixture
def m_overview_use_case(panel_overview: GetPanelOverviewOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.api.v1.sensors.GetPanelOverviewUseCase') as use_case:
        use_case.init.return_value.execute.return_value = panel_overview
        yield use_case


def test_sensors_api_returns_sensor_readings(f_client: Client, m_overview_use_case: MagicMock) -> None:
    response = f_client.get('/api/v1/sensors/')

    assert (response.status_code, response.json(), m_overview_use_case.init.call_count) == (
        200,
        [
            {
                'slug': 'air-second-floor',
                'name': 'Воздух: 2 этаж',
                'kind': 'temperature_sensor',
                'temperature': '19.8',
                'humidity': None,
                'available': False,
                'age_seconds': 640,
            },
            {
                'slug': 'air-kitchen',
                'name': 'Воздух: кухня',
                'kind': 'temperature_sensor',
                'temperature': '21.3',
                'humidity': '45.2',
                'available': True,
                'age_seconds': 12,
            },
            {
                'slug': 'outdoor',
                'name': 'Улица',
                'kind': 'temperature_sensor',
                'temperature': '-3.4',
                'humidity': None,
                'available': True,
                'age_seconds': 20,
            },
        ],
        1,
    )


def test_sensors_api_requires_login(f_anonymous_client: Client) -> None:
    assert f_anonymous_client.get('/api/v1/sensors/').status_code == 403
