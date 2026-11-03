from collections.abc import Iterator
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.test import Client

from apps.panel.usecases.get_panel_overview import GetPanelOverviewOutputDTO

pytestmark = pytest.mark.django_db


@pytest.fixture
def m_overview_use_case(house_overview: GetPanelOverviewOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.api.v1.rooms.GetPanelOverviewUseCase') as use_case:
        use_case.init.return_value.execute.return_value = house_overview
        yield use_case


def test_rooms_api_returns_rooms_in_floor_order(f_client: Client, m_overview_use_case: MagicMock) -> None:
    response = f_client.get('/api/v1/rooms/')

    assert (response.status_code, response.json(), m_overview_use_case.init.call_count) == (
        200,
        [
            {
                'slug': 'open-space',
                'name': 'Гостиная, кухня и прихожая',
                'floor': 'floor-1',
                'zones': ['kitchen'],
                'devices': ['air-kitchen', 'plug-boiler-1f', 'thermostat-kitchen'],
            },
            {
                'slug': 'bedroom',
                'name': 'Спальня',
                'floor': 'floor-2',
                'zones': [],
                'devices': ['air-second-floor'],
            },
            {
                'slug': 'office',
                'name': 'Кабинет',
                'floor': 'floor-2',
                'zones': [],
                'devices': [],
            },
        ],
        1,
    )


def test_rooms_api_requires_login(f_anonymous_client: Client) -> None:
    assert f_anonymous_client.get('/api/v1/rooms/').status_code == 403
