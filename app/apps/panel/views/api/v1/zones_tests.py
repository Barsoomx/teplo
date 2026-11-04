from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime
from unittest import mock
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
from django.test import Client

from apps.panel.usecases.get_panel_overview import GetPanelOverviewOutputDTO

pytestmark = pytest.mark.django_db

HELD_UNTIL = datetime(2026, 11, 4, 18, 40, tzinfo=ZoneInfo('Europe/Moscow'))
KITCHEN = {
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
    'air_control_live': False,
    'path': 'air_dry_run',
    'air_target': '22.0',
    'room': None,
    'manual_until': None,
}


@pytest.fixture
def m_overview_use_case(panel_overview: GetPanelOverviewOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.api.v1.zones.GetPanelOverviewUseCase') as use_case:
        use_case.init.return_value.execute.return_value = panel_overview
        yield use_case


@pytest.fixture
def m_held_overview_use_case(panel_overview: GetPanelOverviewOutputDTO) -> Iterator[MagicMock]:
    kitchen = replace(panel_overview.zones[0], manual_until=HELD_UNTIL)
    with mock.patch('apps.panel.views.api.v1.zones.GetPanelOverviewUseCase') as use_case:
        use_case.init.return_value.execute.return_value = replace(panel_overview, zones=(kitchen,))
        yield use_case


def test_zones_api_returns_overview(f_client: Client, m_overview_use_case: MagicMock) -> None:
    response = f_client.get('/api/v1/zones/')

    assert (response.status_code, response.json(), m_overview_use_case.init.call_count) == (200, [KITCHEN], 1)


def test_zones_api_gives_the_end_of_a_hold_by_hand(f_client: Client, m_held_overview_use_case: MagicMock) -> None:
    response = f_client.get('/api/v1/zones/')

    assert (response.status_code, response.json(), m_held_overview_use_case.init.call_count) == (
        200,
        [{**KITCHEN, 'manual_until': '2026-11-04T18:40:00+03:00'}],
        1,
    )


def test_zones_api_requires_login(f_anonymous_client: Client) -> None:
    assert f_anonymous_client.get('/api/v1/zones/').status_code == 403
